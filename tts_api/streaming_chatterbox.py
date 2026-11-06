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
from RealtimeTTS import ChatterboxEngine
from RealtimeTTS.engines.base_engine import BaseEngine
from chatterbox.tts_turbo import punc_norm, S3GEN_SIL
from chatterbox.models.s3gen.s3gen import S3Token2Mel

SPEECH_VOCAB = 6561          # tokens >= this are special (start/stop) and can't be vocoded
SAMPLES_PER_TOKEN = 960      # 25 tokens/s at 24 kHz
LOOKAHEAD = 3                # flow drops the last 3 tokens when finalize=False


class StreamingChatterboxEngine(ChatterboxEngine):

    def __init__(self, *args, first_chunk_tokens=24, chunk_tokens=32,
                 crossfade_ms=20, watermark=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.first_chunk_tokens = first_chunk_tokens
        self.chunk_tokens = chunk_tokens
        self.crossfade = int(self.sampling_rate * crossfade_ms / 1000)
        # Chatterbox's Perth watermark (marks the audio as AI-generated); stock generate() adds it,
        # we bypass generate() so apply it ourselves, per chunk (~10ms each)
        self.watermark = watermark
        self.edge_margin = int(self.sampling_rate * 0.1)  # 100ms

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
            mels = S3Token2Mel.forward(
                s3gen, tokens, ref_wav=None, ref_sr=None, ref_dict=self._model.conds.gen,
                n_cfm_timesteps=2, finalize=True, noised_mels=noise[:, :, :2 * len(tokens)],
            )
            wav, source = s3gen.hift_inference(mels.to(dtype=s3gen.dtype), cache_source)
        wav = wav.squeeze(0)
        if not finalize:
            keep = (len(tokens) - LOOKAHEAD) * SAMPLES_PER_TOKEN
            wav, source = wav[:keep], source[:, :, :keep]
        fade = s3gen.trim_fade.to(wav.device)
        n = min(len(fade), len(wav))
        wav[:n] *= fade[:n]
        wav = wav.float().cpu().numpy()
        if self.watermark:
            wav = self._apply_watermark(wav)
        return wav, source

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
        try:
            if self.voice is not None and self.voice.audio_prompt_path:
                self.set_voice(self.voice)
            model = self._model
            device = model.device
            text_tokens = model.tokenizer(punc_norm(text), return_tensors="pt").input_ids.to(device)

            seed = int(torch.randint(0, 2**31 - 1, (1,)).item())
            noise = torch.randn(1, 80, 2 * 1010, device=device, dtype=model.s3gen.dtype)
            tokens = []
            sent = 0                # samples already queued
            held = np.zeros(0, np.float32)  # tail kept back to crossfade into the next pass
            source = None
            next_at = self.first_chunk_tokens

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

            for token in self._speech_tokens(text_tokens):
                if self.stop_synthesis_event.is_set():
                    return True
                if token.item() < SPEECH_VOCAB:
                    tokens.append(token.view(1))
                if len(tokens) >= next_at:
                    wav, src = self._vocode(torch.cat(tokens), False, noise, seed,
                                            source[:, :, :sent] if source is not None else None)
                    source = src
                    push(wav, final=False)
                    next_at = len(tokens) + self.chunk_tokens

            if tokens and not self.stop_synthesis_event.is_set():
                wav, _ = self._vocode(torch.cat(tokens), True, noise, seed,
                                      source[:, :, :sent] if source is not None else None)
                push(wav, final=True)
            return True
        except Exception:
            logging.exception("Streaming Chatterbox synthesis failed")
            return False
