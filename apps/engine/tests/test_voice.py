"""Which voice the in-play character speaks as. Run from apps/engine:
    ../../.venv/Scripts/python.exe -m pytest tests -q
"""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import controller.memory as memory_module
import modules.tts.tts_module as tts_module
from controller.memory import load_character, voice_engine, voice_file
from modules.tts.tts_module import TTSModule

ROW = {"slug": "ohm", "name": "Ohm", "personality": "You are Ohm.", "configs": {}, "voice": {"file": "lance.wav"}}


def voice_param(url):
    return parse_qs(urlparse(url).query, keep_blank_values=True)["voice"]


def test_voice_file_reads_the_voice_column():
    assert voice_file(ROW) == "lance.wav"
    assert voice_file({"voice": {}}) is None
    assert voice_file({"voice": None}) is None
    assert voice_file(None) is None


def test_load_character_includes_the_voice(monkeypatch):
    monkeypatch.setattr(memory_module.memory, "get_character", lambda slug: ROW)
    assert load_character("ohm")["voice"] == "lance.wav"


def test_local_fallback_has_the_built_in_voice(monkeypatch):
    monkeypatch.setattr(memory_module.memory, "get_character", lambda slug: None)
    assert load_character("ohm", fallback={"name": "Ohm"})["voice"] is None


def test_every_reply_asks_for_the_characters_voice():
    tts = TTSModule(SimpleNamespace(slug="ohm", name="Ohm", voice="lance.wav"))
    assert voice_param(tts.ws_url()) == ["lance.wav"]


def test_built_in_voice_is_sent_as_empty():
    # Empty (not left out) so the server drops the last character's voice
    tts = TTSModule(SimpleNamespace(slug="ohm", name="Ohm", voice=None))
    assert voice_param(tts.ws_url()) == [""]


def test_a_new_voice_on_the_card_is_picked_up_after_the_ttl(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(tts_module.time, "monotonic", lambda: now[0])
    rows = {"ohm": ROW}
    monkeypatch.setattr(tts_module.memory, "get_character", lambda slug: rows.get(slug))
    tts = TTSModule(SimpleNamespace(slug="ohm", name="Ohm", voice=None))

    assert tts.voice() == ""  # still cached from startup
    now[0] += tts_module.VOICE_TTL
    assert tts.voice() == "lance.wav"

    rows.clear()  # NAS down: keep the voice we had
    now[0] += tts_module.VOICE_TTL
    assert tts.voice() == "lance.wav"


# TTS engine per card

KOKORO = {**ROW, "voice": {"engine": "kokoro", "preset": "am_michael"}}


def test_the_card_says_which_engine_and_old_cards_are_chatterbox():
    assert (voice_engine(KOKORO), voice_file(KOKORO)) == ("kokoro", "am_michael")
    assert (voice_engine(ROW), voice_file(ROW)) == ("chatterbox", "lance.wav")  # older card
    assert voice_engine({"voice": {"engine": "nonsense", "file": "x.wav"}}) == "chatterbox"
    assert voice_file({"voice": {"engine": "kokoro"}}) is None  # Kokoro's default voice
    assert voice_file({"voice": {"engine": "kokoro", "file": "lance.wav"}}) is None


def test_load_character_brings_the_engine(monkeypatch):
    monkeypatch.setattr(memory_module.memory, "get_character", lambda slug: KOKORO)
    c = load_character("ohm")
    assert (c["voice_engine"], c["voice"]) == ("kokoro", "am_michael")


def test_every_reply_asks_for_the_engine_too_and_kokoro_has_no_tags(monkeypatch):
    tts = TTSModule(SimpleNamespace(slug="ohm", name="Ohm", voice="am_michael", voice_engine="kokoro"))
    url = parse_qs(urlparse(tts.ws_url()).query, keep_blank_values=True)
    assert (url["engine"], url["voice"]) == (["kokoro"], ["am_michael"])
    assert not tts.tags()
    assert TTSModule(SimpleNamespace(slug="ohm", name="Ohm", voice=None)).tags()
