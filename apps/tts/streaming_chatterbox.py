"""Chatterbox Turbo engine that streams audio *within* a sentence.

The stock RealtimeTTS ChatterboxEngine waits for T3 to finish every speech token
of a sentence before vocoding it, so the first audio arrives only after
(sentence length / ~5x realtime). Here the T3 loop is run by hand and the tokens
generated so far are vocoded every few dozen steps, so playback can start after
~first_chunk_tokens tokens (~0.3 s) no matter how long the sentence is.

Each vocode pass re-runs S3Gen over *all* tokens so far (it's cheap: ~0.1 s for a
few seconds of audio) with the same noise and HiFiGAN source, so the region that
was already sent comes out (nearly) identical and the seam is crossfaded.
"""
import logging
import math
import os
import time

import numpy as np
import torch
import torch.nn.functional as F
from transformers.generation.logits_process import (
    LogitsProcessorList,
    RepetitionPenaltyLogitsProcessor,
    TemperatureLogitsWarper,
    TopKLogitsWarper,
    TopPLogitsWarper,
)
from RealtimeTTS import ChatterboxEngine, ChatterboxVoice
from RealtimeTTS.engines.base_engine import BaseEngine
from chatterbox.tts_turbo import punc_norm, S3GEN_SIL
from chatterbox.models.s3gen.s3gen import S3Token2Mel
from spoken import spoken

SPEECH_VOCAB = 6561          # tokens >= this are special (start/stop) and can't be vocoded
SAMPLES_PER_TOKEN = 960      # 25 tokens/s at 24 kHz
LOOKAHEAD = 3                # flow drops the last 3 tokens when finalize=False


class StreamingChatterboxEngine(ChatterboxEngine):

    def __init__(self, voice=None, *args, first_chunk_tokens=12, chunk_tokens=32, min_chunk_tokens=6,
                 low_water_ms=400, crossfade_ms=20, watermark=True, speed=0.95, **kwargs):
        super().__init__(None, *args, **kwargs)
        # Chatterbox Turbo's own voice (conds.pt), to go back to after a cloned one
        self._builtin_conds = self._model.conds
        self._voice_conds = {}  # (clip path, mtime) -> its encoded conditionals
        self.use_voice(voice)
        # Speaking rate: < 1 is slower. Done by stretching the mels in time (CosyVoice's method), so pitch stays put
        self.speed = speed
        # When to vocode: the first pass after first_chunk_tokens (small, so audio starts fast). After that,
        # every chunk_tokens, or sooner (min_chunk_tokens) if the listener is about to run out of audio
        # (less than low_water_ms still queued). Fewer passes when there's slack, small ones when there isn't.
        self.first_chunk_tokens = first_chunk_tokens
        self.chunk_tokens = chunk_tokens
        self.min_chunk_tokens = min_chunk_tokens
        self.low_water = low_water_ms / 1000
        # How long a vocode pass and a T3 step take lately (moving averages, seconds). A pass costs about
        # the same no matter how few tokens it adds (the CFM decoder re-runs over the whole voice prompt),
        # so when the GPU is shared and everything slows down, small passes can't keep up with playback.
        self._pass_s = 0.1
        self._token_s = 0.01
        # Estimate of where playback is: audio sent since playback (re)started, and when it started.
        # Carries across sentences, so a sentence that starts while the last one is still playing can wait.
        self._play_start = 0.0
        self._play_audio = 0.0
        self.crossfade = int(self.sampling_rate * crossfade_ms / 1000)
        # Chatterbox's Perth watermark (marks the audio as AI-generated); stock generate() adds it,
        # we bypass generate() so apply it ourselves, per chunk (~10ms each)
        self.watermark = watermark
        self.edge_margin = int(self.sampling_rate * 0.1)  # 100ms

    def use_voice(self, path=None):
        """Speak as the reference clip at `path` (5s+ of one person talking), or the built-in voice for None.
        Each clip is encoded once (~1s) and kept, so switching back to a character later is instant.
        Raises (and keeps the current voice) when the clip can't be used, e.g. it's under 5 seconds."""
        if path is None:
            self._model.conds = self._builtin_conds
            self.voice = None
            self._prepared_voice_path = None
            return
        path = os.path.abspath(path)
        key = (path, os.path.getmtime(path))  # a clip replaced under the same name gets encoded again
        if key not in self._voice_conds:
            logging.info("Encoding Chatterbox prompt: %s", path)
            self._model.prepare_conditionals(path, exaggeration=self.exaggeration, norm_loudness=self.norm_loudness)
            self._voice_conds[key] = self._model.conds
        self._model.conds = self._voice_conds[key]
        self.voice = ChatterboxVoice(audio_prompt_path=path)
        self._prepared_voice_path = path  # so synthesize()'s set_voice() sees it's already loaded

    # ── T3: speech tokens, one at a time ──────────────────────────────────────
    def _speech_tokens(self, text_tokens, max_gen_len=1000):
        """Same sampling as T3.inference_turbo, but yields each token as it's made."""
        t3 = self._model.t3
        procs = LogitsProcessorList()
        if self.temperature > 0 and self.temperature != 1.0:
            procs.append(TemperatureLogitsWarper(self.temperature))
        if self.top_k > 0:
            procs.append(TopKLogitsWarper(self.top_k))
        if self.top_p < 1.0:
            procs.append(TopPLogitsWarper(self.top_p))
        if self.repetition_penalty != 1.0:
            procs.append(RepetitionPenaltyLogitsProcessor(self.repetition_penalty))

        start = t3.hp.start_speech_token * torch.ones_like(text_tokens[:, :1])
        embeds, _ = t3.prepare_input_embeds(
            t3_cond=self._model.conds.t3, text_tokens=text_tokens,
            speech_tokens=start, cfg_weight=0.0,
        )
        out = t3.tfmr(inputs_embeds=embeds, use_cache=True)
        past = out.past_key_values
        logits = t3.speech_head(out[0][:, -1:])
        generated = start
        for _ in range(max_gen_len):
            probs = F.softmax(procs(generated, logits[:, -1, :]), dim=-1)
            token = torch.multinomial(probs, num_samples=1)
            if torch.all(token == t3.hp.stop_speech_token):
                return
            generated = torch.cat([generated, token], dim=1)
            yield token
            out = t3.tfmr(inputs_embeds=t3.speech_emb(token), past_key_values=past, use_cache=True)
            past = out.past_key_values
            logits = t3.speech_head(out[0])

    # ── S3Gen: tokens -> waveform, re-run over the growing token list ─────────
    def _vocode(self, tokens, finalize, noise, seed, cache_source):
        s3gen = self._model.s3gen
        if finalize:
            sil = torch.tensor([S3GEN_SIL] * 3, device=tokens.device, dtype=tokens.dtype)
            tokens = torch.cat([tokens, sil])
        # Same seed every pass so the prompt-region noise inside the decoder matches too
        with torch.random.fork_rng(devices=[s3gen.device]):
            torch.manual_seed(seed)
            # ? finalize=False is broken in this chatterbox build (trims features but not the
            # mask), so always finalize and drop the last LOOKAHEAD tokens' audio ourselves
            if self.speed == 1.0:
                mels = S3Token2Mel.forward(
                    s3gen, tokens, ref_wav=None, ref_sr=None, ref_dict=self._model.conds.gen,
                    n_cfm_timesteps=2, finalize=True, noised_mels=noise[:, :, :2 * len(tokens)],
                )
            else:
                mels = self._slow_mels(tokens, noise)
            wav, source = s3gen.hift_inference(mels.to(dtype=s3gen.dtype), cache_source)
        wav = wav.squeeze(0)
        if not finalize:
            keep = int((len(tokens) - LOOKAHEAD) * SAMPLES_PER_TOKEN / self.speed)
            wav, source = wav[:keep], source[:, :, :keep]
        fade = s3gen.trim_fade.to(wav.device)
        n = min(len(fade), len(wav))
        wav[:n] *= fade[:n]
        wav = wav.float().cpu().numpy()
        if self.watermark:
            wav = self._apply_watermark(wav)
        return wav, source

    def _slow_mels(self, tokens, noise):
        """CausalMaskedDiffWithXvec.inference (finalize=True, batch of 1), but the encoder output for the
        generated part is stretched in time *before* the CFM decoder, so the decoder draws natural mels
        at the slower pace (stretching the finished mels instead smears them and the vocoder hisses)."""
        s3gen = self._model.s3gen
        flow = s3gen.flow
        ref = {k: (torch.from_numpy(v) if isinstance(v, np.ndarray) else v) for k, v in self._model.conds.gen.items()}
        ref = {k: (v.to(device=s3gen.device) if torch.is_tensor(v) else v) for k, v in ref.items()}

        embedding = F.normalize(torch.atleast_2d(ref["embedding"]).to(s3gen.dtype), dim=1)
        embedding = flow.spk_embed_affine_layer(embedding)
        prompt_feat = ref["prompt_feat"].to(s3gen.dtype)
        token = torch.cat([ref["prompt_token"], torch.atleast_2d(tokens)], dim=1)
        token_len = torch.tensor([token.shape[1]], device=token.device)

        h, _ = flow.encoder(flow.input_embedding(token.long()), token_len)
        h = flow.encoder_proj(h)
        mel_len1 = prompt_feat.shape[1]
        # Fixed scale factor (not a target size) so every pass maps the already-sent prefix to the
        # same frames, otherwise the seams with earlier passes would drift
        gen = F.interpolate(h[:, mel_len1:].transpose(1, 2).float(), scale_factor=1 / self.speed,
                            mode="linear", recompute_scale_factor=False).to(h.dtype)
        mu = torch.cat([h[:, :mel_len1].transpose(1, 2), gen], dim=2).contiguous()
        mel_len2 = gen.shape[2]

        conds = torch.zeros([1, flow.output_size, mel_len1 + mel_len2], device=mu.device, dtype=mu.dtype)
        conds[:, :, :mel_len1] = prompt_feat.transpose(1, 2)
        mask = torch.ones([1, 1, mel_len1 + mel_len2], device=mu.device, dtype=mu.dtype)
        feat, _ = flow.decoder(mu=mu, mask=mask, spks=embedding, cond=conds, n_timesteps=2,
                               noised_mels=noise[:, :, :mel_len2], meanflow=s3gen.meanflow)
        return feat[:, :, mel_len1:]

    def _apply_watermark(self, wav):
        """Watermark the whole pass, not each emitted piece: Perth works on a spectrogram and
        smears the edges of whatever it's given, so the seams have to sit mid-signal."""
        n = len(wav)
        # ? Perth's STFT padding crashes on very short input, so pad it out
        padded = np.pad(wav, (0, max(0, 8192 - n)))
        return np.asarray(self._model.watermarker.apply_watermark(padded, sample_rate=self.sampling_rate),
                          dtype=np.float32)[:n]

    def _emit(self, audio):
        if audio.size and not self.stop_synthesis_event.is_set():
            self.queue.put((np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
            now = time.perf_counter()
            if self._ahead(now) <= 0:
                # Everything sent so far has played (or this is a new reply), playback restarts from here
                self._play_start, self._play_audio = now, 0.0
            self._play_audio += audio.size / self.sampling_rate

    def _ahead(self, now=None):
        """Seconds of audio sent but (probably) not played yet.
        ? Playback really starts a bit after we send, so this errs on the low side, which is the safe side"""
        return self._play_audio - ((now or time.perf_counter()) - self._play_start)

    def _tokens_to_keep_up(self, headroom=1.5):
        """Fewest new tokens a pass has to add for the audio it makes to outlast the time it takes
        (generating those tokens plus the pass itself), with some headroom.
        ? Without this, running low meant vocoding every min_chunk_tokens, and with the GPU busy
        (LLM, game, stream encoder) each pass took longer than the audio it added: ~0.25s of
        speech, then ~0.35s of nothing, over and over"""
        per_token = SAMPLES_PER_TOKEN / self.speed / self.sampling_rate   # seconds of audio per token
        spare = per_token - headroom * self._token_s
        if spare <= 0:
            return self.chunk_tokens   # T3 alone can't keep up, so make passes as rare as possible
        return min(self.chunk_tokens, math.ceil(headroom * self._pass_s / spare))

    def _trim_tail(self, audio):
        """Drop trailing silence (the stock engine trimmed each sentence; we can only trim the end)."""
        loud = np.nonzero(np.abs(audio) > self.silence_threshold)[0]
        if not loud.size:
            return audio[:0]
        end = min(len(audio), loud[-1] + int(self.sampling_rate * self.extra_end_ms / 1000))
        audio = audio[:end].copy()
        n = min(len(audio), int(self.sampling_rate * self.fade_out_ms / 1000))
        if n:
            audio[-n:] *= np.linspace(1.0, 0.0, n, dtype=audio.dtype)
        return audio

    @torch.inference_mode()
    def synthesize(self, text: str, sentence_count: int = 0) -> bool:
        BaseEngine.synthesize(self, text, sentence_count)
        if self.stop_synthesis_event.is_set():
            return True
        # Slang said as words (spoken.py)
        text = spoken(text)
        if not text.strip():
            return True
        try:
            if self.voice is not None and self.voice.audio_prompt_path:
                self.set_voice(self.voice)
            model = self._model
            device = model.device
            text_tokens = model.tokenizer(punc_norm(text), return_tensors="pt").input_ids.to(device)

            seed = int(torch.randint(0, 2**31 - 1, (1,)).item())
            noise = torch.randn(1, 80, int(2 * 1010 / self.speed) + 2, device=device, dtype=model.s3gen.dtype)
            tokens = []
            sent = 0                # samples already queued
            held = np.zeros(0, np.float32)  # tail kept back to crossfade into the next pass
            source = None
            vocoded = 0             # how many tokens the last vocode pass covered

            first = True

            def push(wav, final):
                nonlocal sent, held, first
                if first:
                    # Skip leading silence on the sentence's first chunk, with a short fade-in
                    first = False
                    loud = np.nonzero(np.abs(wav) > self.silence_threshold)[0]
                    if loud.size:
                        sent = max(0, loud[0] - int(self.sampling_rate * self.extra_start_ms / 1000))
                    n = min(len(wav) - sent, int(self.sampling_rate * self.fade_in_ms / 1000))
                    if n > 0:
                        wav = wav.copy()
                        wav[sent:sent + n] *= np.linspace(0.0, 1.0, n, dtype=wav.dtype)
                new = wav[sent:]
                if held.size:
                    k = min(len(held), len(new))
                    ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)
                    new = new.copy()
                    new[:k] = held[:k] * (1 - ramp) + new[:k] * ramp
                if final:
                    self._emit(self._trim_tail(new))
                    return
                # Hold back the right edge (watermark/vocoder edge effects live there, the next
                # pass re-renders it), keeping just a crossfade's worth to blend into that pass
                cut = max(0, len(new) - self.edge_margin - self.crossfade)
                out, held = new[:cut], new[cut:cut + self.crossfade].copy()
                self._emit(out)
                sent += cut

            step = None             # when the last T3 step ended (None: the first one also reads the text, don't count it)
            for token in self._speech_tokens(text_tokens):
                if self.stop_synthesis_event.is_set():
                    return True
                # token.item() waits for the GPU, so this is the real time of one T3 step
                is_speech = token.item() < SPEECH_VOCAB
                now = time.perf_counter()
                if step is not None:
                    self._token_s += 0.05 * (now - step - self._token_s)
                if is_speech:
                    tokens.append(token.view(1))
                new = len(tokens) - vocoded
                need = max(self.first_chunk_tokens if not vocoded else self.min_chunk_tokens,
                           self._tokens_to_keep_up())
                if new >= self.chunk_tokens or (new >= need and self._ahead() < self.low_water):
                    wav, src = self._vocode(torch.cat(tokens), False, noise, seed,
                                            source[:, :, :sent] if source is not None else None)
                    # (_vocode ends by copying the audio to the CPU, so the GPU work is done by here)
                    self._pass_s += 0.3 * (time.perf_counter() - now - self._pass_s)
                    source = src
                    push(wav, final=False)
                    vocoded = len(tokens)
                step = time.perf_counter()

            if tokens and not self.stop_synthesis_event.is_set():
                wav, _ = self._vocode(torch.cat(tokens), True, noise, seed,
                                      source[:, :, :sent] if source is not None else None)
                push(wav, final=True)
            return True
        except Exception:
            logging.exception("Streaming Chatterbox synthesis failed")
            return False
