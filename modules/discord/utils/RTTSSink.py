from discord.ext import voice_recv
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import soundfile as sf
import soxr
import time
from modules.stt.parakeet import get_models
import utils.latency as latency

# Discord gives 48kHz stereo 16-bit PCM in 20ms frames. Parakeet + Silero want 16kHz mono.
DISCORD_RATE = 48000
STT_RATE = 16000
CHUNK = 512             # Silero takes 512 samples (32ms) at a time
CHUNK_MS = 32
CONTEXT = 64            # ...plus the last 64 samples of the previous chunk

# Voice done detection
EARLY_TRANSCRIBE_MS = 100 # Start transcribing after this much silence so it's ready by QUICK_SILENCE_MS
QUICK_SILENCE_MS = 200  # If what they said ends like a sentence, reply after this (transcription takes ~60ms)
SILENCE_MS = 900        # Otherwise wait this long in case they're mid-thought
BARGE_IN_MS = 600       # This much speech while the bot is talking pauses it (shorter = "yeah", "mhm")
MIN_SPEECH_MS = 250     # Ignore anything shorter than this (coughs, clicks)
PRE_ROLL_MS = 200       # Keep a bit of audio from before speech started so the first word isn't cut off
SPEECH_THRESHOLD = 0.5  # Silero speech probability 0-1, higher = more strict
DEBUG = True            # Print what the sink is hearing

class UserStream:
    """Audio buffer + speaking state for a single user"""
    def __init__(self):
        self.audio = []
        # ? One resampler per user so it keeps state between frames, resampling each 20ms frame alone adds clicks
        self.resampler = soxr.ResampleStream(DISCORD_RATE, STT_RATE, 1, dtype="float32")
        self.pending = np.zeros(0, dtype=np.float32)   # Samples waiting to fill a chunk
        self.context = np.zeros(CONTEXT, dtype=np.float32)
        self.state = np.zeros((2, 1, 128), dtype=np.float32) # Silero's memory, one per user
        self.speech_ms = 0
        self.silence_ms = 0
        self.speaking = False
        self.early = None # Early transcription started after QUICK_SILENCE_MS
        self.barged = False # speech_cb already fired for this utterance

class CustomSpeechRecognitionSink(voice_recv.AudioSink):
    def __init__(self, *, text_cb=None, speech_cb=None, recognizer=None):
        super().__init__()
        self.text_cb = text_cb
        # Called (on the bot's event loop) once someone has been talking for BARGE_IN_MS, before they finish
        self.speech_cb = speech_cb
        self.recognizer_name = recognizer

        self.model, self.vad = get_models()
        self.streams = {}
        # ? Transcribe off the voice thread so we don't drop incoming packets
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.frames = 0
        # ? Saves everything the sink hears so it can be replayed offline
        self.recording = sf.SoundFile("stt_debug.wav", "w", STT_RATE, 1) if DEBUG else None

    def wants_opus(self):
        return False

    def write(self, user, data):
        self.frames += 1
        if DEBUG and self.frames % 250 == 0: # About every 5s
            print(f"[stt] frames={self.frames} user={user} peak={np.abs(np.frombuffer(data.pcm, dtype=np.int16)).max()}")
            try:
                dave = getattr(self.voice_client._connection, "dave_session", None)
                print(f"[stt] dave_ready={dave.ready if dave else None} health={self.voice_client._reader.health()}")
            except Exception as e:
                print(f"[stt] couldn't read dave health: {e!r}")
        if user is None:
            return

        # Stereo 48k -> mono 16k
        stream = self.streams.setdefault(user, UserStream())
        pcm = np.frombuffer(data.pcm, dtype=np.int16).reshape(-1, 2).mean(axis=1).astype(np.float32) / 32768.0
        pcm = stream.resampler.resample_chunk(pcm)
        if self.recording:
            self.recording.write(pcm)
            self.recording.flush()
        stream.pending = np.concatenate([stream.pending, pcm])

        # ? Discord frames are 20ms but Silero wants 32ms, so run every full chunk we have
        while len(stream.pending) >= CHUNK:
            chunk, stream.pending = stream.pending[:CHUNK], stream.pending[CHUNK:]
            if self.process_chunk(user, stream, chunk):
                self.streams[user] = UserStream()
                break

    def process_chunk(self, user, stream, chunk):
        """Run one chunk through Silero. Returns True when the user is done talking"""
        prob, stream.state = self.vad.run(["output", "stateN"], {
            "input": np.concatenate([stream.context, chunk])[None, :],
            "state": stream.state,
            "sr": np.array(STT_RATE, dtype=np.int64)
        })
        stream.context = chunk[-CONTEXT:]
        stream.audio.append(chunk)

        if prob[0][0] >= SPEECH_THRESHOLD:
            if DEBUG and not stream.speaking: print(f"[stt] {user} started talking")
            stream.speaking = True
            stream.speech_ms += CHUNK_MS
            stream.silence_ms = 0
            stream.early = None # They kept talking, throw out the early guess
            if self.speech_cb and not stream.barged and stream.speech_ms >= BARGE_IN_MS:
                stream.barged = True
                self.client.loop.call_soon_threadsafe(self.speech_cb, user)
            return False

        if not stream.speaking:
            # Not talking yet, only keep the pre-roll
            stream.audio = stream.audio[-(PRE_ROLL_MS // CHUNK_MS):]
            return False

        stream.silence_ms += CHUNK_MS
        if stream.speech_ms < MIN_SPEECH_MS:
            if DEBUG and stream.silence_ms >= SILENCE_MS: print(f"[stt] dropped, only {stream.speech_ms}ms of speech")
            return stream.silence_ms >= SILENCE_MS # Too short, wait it out and drop it

        # Transcribe early. If it reads like a finished sentence we don't need to wait the full SILENCE_MS
        # ? Transcription takes ~150ms, so start it before QUICK_SILENCE_MS and have it ready by then
        if stream.silence_ms >= EARLY_TRANSCRIBE_MS and stream.early is None:
            stream.early = self.pool.submit(self.recognize, np.concatenate(stream.audio))

        if (stream.silence_ms >= QUICK_SILENCE_MS and stream.early and stream.early.done()
                and stream.early.result().strip().endswith((".", "?", "!"))):
            self.send(user, stream.early.result(), stream.silence_ms)
            return True

        if stream.silence_ms >= SILENCE_MS:
            audio = np.concatenate(stream.audio)
            self.pool.submit(self.transcribe, user, audio, stream.silence_ms, time.perf_counter())
            return True

        return False

    def recognize(self, audio):
        start = time.time()
        if DEBUG: print(f"[stt] transcribing {len(audio) / STT_RATE:.1f}s of audio")
        try:
            text = self.model.recognize(audio)
        except Exception as e:
            print(f"[stt] transcribe failed: {e!r}")
            return ""
        if DEBUG: print(f"[stt] took {time.time() - start:.2f}s")
        return text

    def transcribe(self, user, audio, silence_ms, at):
        self.send(user, self.recognize(audio), silence_ms, at)

    def send(self, user, text, silence_ms=0, at=None):
        if DEBUG: print(f"[stt] {user}: {text!r}")
        if text.strip() == "":
            return
        # Speech ended silence_ms before the moment we decided they were done
        latency.start((at or time.perf_counter()) - silence_ms / 1000)
        latency.mark("transcript")
        # ? text_cb touches an asyncio.Queue, so hand it back to the bot's event loop
        self.client.loop.call_soon_threadsafe(self.text_cb, user, text)

    def anyone_speaking(self) -> bool:
        """True while someone is mid-sentence (their words haven't been transcribed yet)"""
        return any(s.speaking for s in list(self.streams.values()))

    def cleanup(self) -> None:
        self.streams = {}
        self.pool.shutdown(wait=False)
        if self.recording: self.recording.close()
