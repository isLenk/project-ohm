"""Reply cleanup, mention conversion and the per-request LoRA list. Run from apps/engine:
    ../../.venv/Scripts/python.exe -m pytest tests -q
"""
import sqlite3
from types import SimpleNamespace

from controller import adapters
from controller.adapters import Adapters, lora_list, norm_path, registry_path, ui_model
from utils.ReplyCleanup import (AtFilter, clean_reply, cut_transcript, outgoing_mentions, readable_mentions,
                                speaker_labels, stop_sequences, strip_leading_label)
from utils.VoiceTags import VoiceTagFilter


# ── One reply, not a transcript ─────────────────────────────────────────────

def test_fake_transcript_is_cut_to_the_first_reply():
    text = "Ohm: yo\nLenk: you there\nOhm: unfortunately..."
    assert clean_reply(text, "Ohm", ["Lenk"]) == "yo"


def test_cut_is_case_insensitive_and_allows_leading_spaces():
    assert cut_transcript("sure thing\n   lenk : wait what", ["Lenk"]) == "sure thing"
    assert cut_transcript("nah\nOHM: anyway", ["Ohm"]) == "nah"


def test_only_the_leading_label_is_stripped():
    assert strip_leading_label("  ohm:  hey", "Ohm") == "hey"
    assert strip_leading_label("Ohm: Ohm: hey", "Ohm") == "Ohm: hey"
    assert strip_leading_label("Ohmygod: lol", "Ohm") == "Ohmygod: lol"


def test_a_leading_label_of_someone_else_is_stripped():
    # He copies the "Name: text" form of the message he's answering
    said = clean_reply("Lenk: [sarcastic] yeah, i know. i'm a menace.", "Ohm", ["Lenk", "KaptainDoosh"])
    assert said == "[sarcastic] yeah, i know. i'm a menace."
    assert clean_reply("KaptainDoosh: [sigh] well...", "Ohm", ["Lenk", "KaptainDoosh"]) == "[sigh] well..."
    assert strip_leading_label("Lenk: Ohm: hey", ["Ohm", "Lenk"]) == "Ohm: hey"
    assert strip_leading_label("Lenk, you there", ["Ohm", "Lenk"]) == "Lenk, you there"


def test_voice_filter_drops_a_leading_label_before_the_tts():
    def run(text, names, chunk=3):
        f = VoiceTagFilter("Ohm")
        f.set_speakers(names)
        out = "".join(f.feed(text[i:i + chunk]) for i in range(0, len(text), chunk))
        return out + f.flush()

    assert run("Lenk: [sarcastic] yeah", ["Ohm", "Lenk"]) == " [sarcastic] yeah"
    assert run("Ohm: hey", ["Ohm", "Lenk"]) == " hey"
    assert run("Lenkster: hi", ["Ohm", "Lenk"]) == "Lenkster: hi"
    # Past the first line only his own label goes: a "Lenk:" line is a transcript, and cut_transcript needs it
    assert run("hey\nLenk: what", ["Ohm", "Lenk"]) == "hey\nLenk: what"
    assert run("hey\nOhm: what", ["Ohm", "Lenk"]) == "hey\n what"


def test_multiline_reply_without_labels_is_kept():
    assert clean_reply("line one\nline two: with a colon", "Ohm", ["Lenk"]) == "line one\nline two: with a colon"


def test_label_must_be_whole_name():
    # "Lenkster:" isn't "Lenk:"
    assert cut_transcript("hi\nLenkster: yo", ["Lenk"]) == "hi\nLenkster: yo"


def test_cleaning_can_leave_nothing():
    assert clean_reply("Ohm:\nLenk: hello?", "Ohm", ["Lenk"]) == ""


def test_speaker_labels_and_stop_sequences():
    history = [
        {"role": "user", "content": "Lenk: yo\nsecond line: not a label"},
        {"role": "assistant", "content": "Ohm: hey"},
        {"role": "user", "content": "Sam B: sup"},
        {"role": "user", "content": "lenk: again"},
    ]
    labels = speaker_labels(history, ["Mia", "Ohm"])
    assert labels == ["Lenk", "Sam B", "Mia", "Ohm"]
    stops = stop_sequences(labels)
    assert "\nLenk:" in stops and "\nlenk:" in stops
    assert "\nSam B:" in stops and "\nsam b:" in stops
    assert "\nOhm:" in stops and "\nohm:" in stops
    assert len(stops) == len(set(stops))


# ── Mentions ────────────────────────────────────────────────────────────────

def member(id, display, global_name=None, name=None):
    return SimpleNamespace(id=id, display_name=display, global_name=global_name, name=name or display.lower())


LENK = member(1, "Lenk", "Lenk G", "lenk_g")
LENK_JR = member(2, "Lenk Jr")
SAM = member(3, "Sam")


def test_incoming_mentions_use_display_names():
    text = readable_mentions("<@1> <@!99> ask <@&5> in <#7>, <@42>", {1: "Lenk", 99: "Ohm"}, {5: "mods"}, {7: "general"})
    assert text == "@Lenk @Ohm ask @mods in #general, <@42>"


def test_leading_mention_of_the_person_replied_to_is_dropped():
    assert outgoing_mentions("@lenk yo", [LENK, SAM], reply_to=LENK) == ("yo", "yo")
    assert outgoing_mentions("@Lenk, @lenk_g: sure", [LENK], reply_to=LENK) == ("sure", "sure")
    # Not the person being replied to: that's a real mention
    assert outgoing_mentions("@sam yo", [LENK, SAM], reply_to=LENK) == ("<@3> yo", "@Sam yo")


def test_mentions_become_pings_longest_name_first():
    out = outgoing_mentions("tell @lenk jr and @LENK G hi", [LENK, LENK_JR], reply_to=SAM)
    assert out == ("tell <@2> and <@1> hi", "tell @Lenk Jr and @Lenk hi")


def test_unknown_mentions_lose_the_at_and_emails_are_left_alone():
    out = outgoing_mentions("@everyone ask @bob at bob@mail.com @ noon", [LENK], reply_to=LENK)
    assert out == ("everyone ask bob at bob@mail.com @ noon", "everyone ask bob at bob@mail.com @ noon")


def test_name_must_end_at_a_word_boundary():
    assert outgoing_mentions("@Samwise hi", [SAM]) == ("Samwise hi", "Samwise hi")
    assert outgoing_mentions("@Sam's turn", [SAM]) == ("<@3>'s turn", "@Sam's turn")


def test_voice_drops_at_even_when_it_arrives_alone():
    f = AtFilter()
    chunks = ["yo ", "@", "lenk", " email a", "@", "b.com (", "@sam)"]
    assert "".join(f.feed(c) for c in chunks) == "yo lenk email a@b.com (sam)"


# ── LoRA per request ────────────────────────────────────────────────────────

LOADED = [{"id": 0, "path": "C:\\Data\\runs\\a\\adapter.gguf", "scale": 1.0},
          {"id": 1, "path": "C:\\Data\\runs\\b\\adapter.gguf", "scale": 1.0}]


def test_lora_list_lists_every_adapter():
    assert lora_list(LOADED, LOADED[1]["path"]) == [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 1.0}]
    assert lora_list(LOADED, "C:/Data/runs/b/adapter.gguf")[1]["scale"] == 1.0  # slashes don't matter
    assert lora_list(LOADED, None) == [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 0.0}]
    assert lora_list([], "x") == []


def test_norm_path():
    assert norm_path("C:\\A\\B.gguf", windows=True) == norm_path("c:/a/b.gguf", windows=True)
    assert norm_path("/A/B.gguf", windows=False) != norm_path("/a/b.gguf", windows=False)


def test_ui_model_handles_missing_fields():
    assert ui_model(None) == {"base_model_id": None, "adapter_id": None}
    assert ui_model({"configs": {"ui": {"model": {}}}}) == {"base_model_id": None, "adapter_id": None}
    row = {"configs": {"ui": {"model": {"base_model_id": "b", "adapter_id": "a"}}}}
    assert ui_model(row) == {"base_model_id": "b", "adapter_id": "a"}


def make_registry(tmp_path):
    db = tmp_path / "registry.sqlite"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE adapters (id TEXT, base_model_id TEXT, name TEXT, gguf_path TEXT)")
        c.execute("CREATE TABLE base_models (id TEXT, gguf_path TEXT)")
        c.execute("INSERT INTO adapters VALUES ('ad-b', 'base', 'b', ?)", (LOADED[1]["path"],))
        c.execute("INSERT INTO adapters VALUES ('ad-gone', 'base', 'gone', 'C:\\Data\\runs\\gone\\adapter.gguf')")
        c.execute("INSERT INTO base_models VALUES ('base', 'C:\\models\\base.gguf')")
    c.close()
    return db


def test_registry_lookup(tmp_path):
    db = make_registry(tmp_path)
    assert registry_path("adapters", "ad-b", db) == LOADED[1]["path"]
    assert registry_path("adapters", "nope", db) is None
    assert registry_path("adapters", None, db) is None
    assert registry_path("adapters", "ad-b", tmp_path / "missing.sqlite") is None


def fake_server(picker, loaded, model_path="C:\\models\\base.gguf"):
    picker._get = lambda path: {"/lora-adapters": loaded, "/props": {"model_path": model_path}}.get(path)


def test_adapters_follow_the_character(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CURATOR_DATA_DIR", str(make_registry(tmp_path).parent))
    rows = {"ohm": {"configs": {"ui": {"model": {"base_model_id": "base", "adapter_id": "ad-b"}}}}}
    picker = Adapters({"slug": "ohm", "name": "Ohm", "base_model_id": "base", "adapter_id": "ad-b"},
                      "http://127.0.0.1:5000/v1", fetch_character=rows.get)
    assert picker.root == "http://127.0.0.1:5000"
    fake_server(picker, LOADED)
    assert picker.lora() == [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 1.0}]
    assert picker.lora(base=True) == [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 0.0}]

    # Adapter removed on the Characters page: picked up once the TTL runs out
    rows["ohm"] = {"configs": {"ui": {"model": {"base_model_id": "base", "adapter_id": None}}}}
    assert picker.lora()[1]["scale"] == 1.0  # still cached
    picker._checked_at -= adapters.CHARACTER_TTL
    assert picker.lora() == [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 0.0}]


def test_adapter_not_loaded_warns_once_and_uses_base(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CURATOR_DATA_DIR", str(make_registry(tmp_path).parent))
    picker = Adapters({"slug": "x", "name": "X", "base_model_id": "base", "adapter_id": "ad-gone"}, "http://h/v1")
    fake_server(picker, LOADED, model_path="C:\\models\\other.gguf")
    for _ in range(3):
        assert picker.lora() == [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 0.0}]
    out = capsys.readouterr().out
    assert out.count("isn't loaded in llama-server") == 1
    assert out.count("llama-server is running") == 1


def test_no_lora_field_when_server_has_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CURATOR_DATA_DIR", str(tmp_path))
    picker = Adapters({"slug": "x", "name": "X"}, "http://h/v1")
    fake_server(picker, None)
    assert picker.lora() is None
    fake_server(picker, [])
    assert picker.lora(base=True) is None


def test_no_tags_at_all_for_a_voice_that_cant_do_them():
    f = VoiceTagFilter("Ohm", max_tags=0)
    assert f.feed("[sigh] again? *laughs* what'd you hit") + f.flush() == " again?  what'd you hit"
