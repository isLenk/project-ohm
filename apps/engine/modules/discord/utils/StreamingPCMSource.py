import threading
import numpy as np
import soxr
import discord

DISCORD_RATE = 48000
# 20 ms of 48 kHz, 16-bit, stereo PCM: what discord.py asks for on every read()
FRAME_BYTES = discord.opus.Encoder.FRAME_SIZE
SILENCE = b"\x00" * FRAME_BYTES


class StreamingPCMSource(discord.AudioSource):
    """One continuous audio source for a whole TTS response.

    Raw mono s16le PCM from the TTS server is fed in as it arrives, resampled
    to 48 kHz stereo on the way in, and read() hands discord.py 20 ms frames.
    Feeding everything through a single source avoids spawning one ffmpeg per
    buffer and the polling gaps between buffers.
    """

    def __init__(self, in_rate=24000, prebuffer_ms=40, on_start=None):
        # ? TTS now sends ~1s chunks at a time, so a long prebuffer only adds delay
        self._on_start = on_start
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._finished = False
        self._started = False
        self._carry = b""
        self._prebuffer = FRAME_BYTES * max(1, prebuffer_ms // 20)
        # ? Stateful resampler, so chunk boundaries don't click
        self._resampler = soxr.ResampleStream(in_rate, DISCORD_RATE, 1, dtype="int16", quality="HQ")

    def _append(self, mono: np.ndarray):
        if mono.size == 0:
            return
        # Duplicate each sample into L/R, giving interleaved stereo
        stereo = np.repeat(mono, 2).tobytes()
        with self._lock:
            self._buf += stereo

    def feed(self, pcm: bytes):
        """Add raw mono 16-bit PCM at in_rate. Called from the event loop."""
        if self._finished:
            return
        pcm = self._carry + pcm
        # Websocket messages can split a sample in half; keep the odd byte for next time
        if len(pcm) % 2:
            pcm, self._carry = pcm[:-1], pcm[-1:]
        else:
            self._carry = b""
        if pcm:
            self._append(self._resampler.resample_chunk(np.frombuffer(pcm, dtype=np.int16)))

    def finish(self):
        """No more audio is coming. Playback ends once the buffer drains."""
        if self._finished:
            return
        self._append(self._resampler.resample_chunk(np.zeros(0, dtype=np.int16), last=True))
        with self._lock:
            self._finished = True

    def clear(self):
        """Drop whatever is buffered and end playback (used on interrupt)."""
        with self._lock:
            self._buf.clear()
            self._finished = True

    def read(self) -> bytes:
        # Runs on discord.py's player thread every 20 ms
        with self._lock:
            if not self._started:
                if len(self._buf) < self._prebuffer and not self._finished:
                    return SILENCE
                self._started = True
                if self._on_start:
                    try: self._on_start()
                    except Exception as e: print(f"on_start failed: {e!r}")

            if len(self._buf) >= FRAME_BYTES:
                frame = bytes(self._buf[:FRAME_BYTES])
                del self._buf[:FRAME_BYTES]
                return frame

            if self._finished:
                if self._buf:
                    frame = bytes(self._buf).ljust(FRAME_BYTES, b"\x00")
                    self._buf.clear()
                    return frame
                return b""  # tells discord.py playback is over

        # Underrun: TTS is still generating the next sentence, so pad with silence
        return SILENCE

    def is_opus(self) -> bool:
        return False
