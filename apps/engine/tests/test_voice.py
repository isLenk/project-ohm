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


# Search fillers

def test_filler_lines_are_made_once_in_the_voice_and_never_twice_in_a_row(monkeypatch):
    import asyncio
    made = []

    async def synthesize(self, text, voice, engine="chatterbox"):
        made.append((text, voice))
        if text == tts_module.SEARCH_FILLERS[-1]:
            raise ConnectionError("TTS hiccup")  # left out, the rest still work
        return f"pcm:{text}".encode()
    monkeypatch.setattr(TTSModule, "_synthesize", synthesize)
    tts = TTSModule(SimpleNamespace(slug="ohm", name="Ohm", voice="lance.wav"))

    assert tts.filler() is None
    asyncio.run(tts.prepare_fillers())
    assert made == [(line, "lance.wav") for line in tts_module.SEARCH_FILLERS]
    picks = [tts.filler() for _ in range(30)]
    assert set(picks) == {f"pcm:{line}".encode() for line in tts_module.SEARCH_FILLERS[:-1]}
    assert all(a != b for a, b in zip(picks, picks[1:]))
    assert not tts.fillers_stale()

    tts._voice = "someone else.wav"
    assert tts.fillers_stale() and tts.filler() is None


def stream_response(monkeypatch, search, answered=False):
    """Whether make_stream_response called on_search()"""
    import modules.discord.DiscordClient as client_module

    class FakeTurn:
        def __init__(self, client, channel, source, said_by=None):
            self.line, self.channel_id = "Lenk: what's the score", 1

        def prompt(self, *args, **kwargs):
            pass
    monkeypatch.setattr(client_module, "Turn", FakeTurn)
    monkeypatch.setattr(client_module.memory, "relevant_memories", lambda character, text: [])
    monkeypatch.setattr(client_module.WebSearch, "answered", lambda query: answered)
    client = SimpleNamespace(character="ohm", people_in=lambda said: [], history=lambda channel_id: [],
                             checks_for=lambda line, history: {"search": search, "screen": False, "to": None},
                             look_at_screen=lambda said, checks: (None, ""))
    calls = []
    client_module.DiscordClient.make_stream_response(client, {"lenk": "what's the score"}, None,
                                                     on_search=lambda: calls.append(1))
    return bool(calls)


def test_the_filler_only_plays_when_the_reply_really_waits_on_a_search(monkeypatch):
    assert stream_response(monkeypatch, search="T1 vs Gen.G score")
    assert not stream_response(monkeypatch, search=None)
    assert not stream_response(monkeypatch, search="T1 vs Gen.G score", answered=True)


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


def test_switching_engine_on_the_card_is_picked_up_and_remakes_the_fillers(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(tts_module.time, "monotonic", lambda: now[0])
    rows = {"ohm": ROW}
    monkeypatch.setattr(tts_module.memory, "get_character", lambda slug: rows.get(slug))
    tts = TTSModule(SimpleNamespace(slug="ohm", name="Ohm", voice="lance.wav"))
    tts._fillers = (("chatterbox", "lance.wav"), [b"pcm"])
    assert tts.tags() and not tts.fillers_stale()

    rows["ohm"] = KOKORO
    now[0] += tts_module.VOICE_TTL
    assert parse_qs(urlparse(tts.ws_url()).query)["engine"] == ["kokoro"]
    assert tts.engine() == "kokoro" and not tts.tags()
    assert tts.fillers_stale() and tts.filler() is None
