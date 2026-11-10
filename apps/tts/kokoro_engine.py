"""Kokoro (hexgrad/Kokoro-82M) for cards that pick it. ~5x faster than Chatterbox, but no clips or [tags]."""
import re

from RealtimeTTS import KokoroEngine
from RealtimeTTS.engines.base_engine import BaseEngine

from spoken import spoken

# English voices, af/am American, bf/bm British. Keep in sync with mainframe's lib/voices.ts
VOICES = (
    "af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica", "af_kore", "af_nicole", "af_nova", "af_river",
    "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx",
    "am_puck", "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george",
    "bm_lewis",
)
DEFAULT_VOICE = "af_heart"  # rated best

_TAG = re.compile(r"\s*\[[^\]]*\]")  # it'd read "[sigh]" out


def kokoro_voice(name: str) -> str:
    """`name`, or DEFAULT_VOICE for "". ValueError if Kokoro doesn't have it"""
    name = name or DEFAULT_VOICE
    if name not in VOICES:
        raise ValueError(f"Kokoro has no voice called {name!r}")
    return name


class OhmKokoroEngine(KokoroEngine):
    def synthesize(self, text: str, sentence_count: int = 0) -> bool:
        # Slang said as words, tags dropped
        text = spoken(_TAG.sub("", text))
        if not text.strip():
            BaseEngine.synthesize(self, text, sentence_count)
            return True
        return super().synthesize(text, sentence_count)
