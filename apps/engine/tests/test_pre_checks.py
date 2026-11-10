"""The one check before each reply (OpenAIModel.pre_checks) and where create() puts the per-turn notes.
Run from apps/engine:
    ../../.venv/Scripts/python.exe -m pytest tests -q
"""
import pytest

import controller.wrappers.openai as openai_module
from controller.wrappers.openai import NO_CHECKS, OpenAIModel


@pytest.fixture
def model():
    return OpenAIModel.__new__(OpenAIModel)  # pre_checks doesn't need a client or a character


@pytest.fixture
def asked(monkeypatch):
    """brain.run swapped for one that records each Task and answers with `asked.answer`."""
    class Fake:
        answer = None
        tasks = []

        def __call__(self, task):
            self.tasks.append(task)
            return self.answer
    fake = Fake()
    fake.tasks = []
    monkeypatch.setattr(openai_module.brain, "run", fake)
    monkeypatch.setattr(openai_module.screen_module, "on", lambda: True)
    return fake


def test_short_or_wordless_messages_skip_the_call(model, asked):
    for text in ["lol", "ok sure", "", "   ", "1 2 3 4", "?? !! ..."]:
        assert model.pre_checks(text) == NO_CHECKS, text
    assert asked.tasks == []


def test_screen_only_asked_with_a_cue_word(model, asked):
    asked.answer = {"search": None}
    assert model.pre_checks("who won the game last night") == {"search": None, "screen": False}
    task = asked.tasks[-1]
    assert "screen" not in task.schema["properties"] and "screen" not in task.system.split("JSON")[-1]
    assert task.role == "classify" and task.local_only and task.temperature == 0

    asked.answer = {"search": None, "screen": True}
    assert model.pre_checks("yo look at this boss") == {"search": None, "screen": True}
    assert asked.tasks[-1].schema["required"] == ["search", "screen"]


def test_screen_not_asked_with_vision_off(model, asked, monkeypatch):
    monkeypatch.setattr(openai_module.screen_module, "on", lambda: False)
    asked.answer = {"search": None, "screen": True}  # even if the model says so
    assert model.pre_checks("what do you think of this") == {"search": None, "screen": False}
    assert "screen" not in asked.tasks[-1].schema["properties"]


def test_parses_the_answer(model, asked):
    asked.answer = {"search": ' "weather in Manila today" ', "screen": False}
    assert model.pre_checks("what's the weather like here") == {"search": "weather in Manila today", "screen": False}
    asked.answer = {"search": "NONE", "screen": "yes"}  # not a real boolean, so no
    assert model.pre_checks("which one should i pick") == {"search": None, "screen": False}
    asked.answer = {"search": 5}
    assert model.pre_checks("how are you doing today")["search"] is None


def test_no_answer_means_no_search_no_screen(model, asked):
    asked.answer = None
    assert model.pre_checks("check out this new patch") == NO_CHECKS
    assert model.search_query("who won the game last night") is None
    assert model.about_screen("look at this") is False


def test_look_up_uses_the_checks_it_is_given(model, asked):
    assert model.look_up("who won the game last night", {"search": None, "screen": False}) is None
    assert asked.tasks == []


# ── Prompt layout ───────────────────────────────────────────────────────────
class Client:
    def __init__(self):
        self.sent = None
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.sent = kwargs
        return kwargs


@pytest.fixture
def chat(monkeypatch):
    m = OpenAIModel.__new__(OpenAIModel)
    m.name, m.model, m.system, m.client = "Ohm", "gemma", "  You are Ohm.  ", Client()
    m.adapters = type("A", (), {"lora": lambda self, base=False: None})()
    monkeypatch.setattr(openai_module.memory, "glossary_for", lambda text: ['"hll": Hell Let Loose'])
    monkeypatch.setattr(openai_module.mood, "line", lambda: "You're hyped up right now.")
    return m


def test_system_prompt_is_only_the_personality(chat):
    history = [{"role": "user", "content": "Lenk: yo"}, {"role": "assistant", "content": "sup"},
               {"role": "user", "content": "Kyle: hll tonight?"}]
    people = [{"name": "Kyle", "notes": ["Kyle plays hll."]}]
    memories = [{"content": "Kyle plays hll."}, {"content": "Lenk hates mornings."}]
    sent = chat.create("Lenk: im in", history=history, people=people, related_memories=memories,
                       summary="Lenk and Kyle are planning a game.", screen="data:image/png;base64,AA")["messages"]
    assert sent[0] == {"role": "system", "content": "You are Ohm."}
    assert sent[1:3] == history[:2]
    last = sent[-1]
    assert last["role"] == "user" and len(sent) == 4  # the two user lines merged
    text = last["content"][0]["text"]
    assert text.startswith("Kyle: hll tonight?\nLenk: im in\n\n(You're hyped up right now.\n\n")
    assert text.endswith(")")
    for block in ['Words people here use', 'What\'s been going on in this chat: Lenk and Kyle are planning a game.',
                  "People who came up", "- Lenk hates mornings.", "You can see the screen"]:
        assert block in text, block
    assert text.count("Kyle plays hll.") == 1  # already under the person, so not again under memories
    assert last["content"][1]["image_url"]["url"].startswith("data:image/png")


def test_nothing_extra_means_no_note(chat, monkeypatch):
    monkeypatch.setattr(openai_module.mood, "line", lambda: "")
    sent = chat.create("Lenk: hi", glossary=False)["messages"]
    assert sent[-1] == {"role": "user", "content": "Lenk: hi"}


def test_a_voice_without_tags_isnt_told_about_them(chat, monkeypatch):
    monkeypatch.setattr(openai_module.mood, "tag_hint", lambda: "If a sound fits, lean toward [sigh].")
    tagged = chat.create("Lenk: ugh", voice=True, glossary=False)["messages"]
    assert "[sigh]" in tagged[0]["content"] and "lean toward [sigh]" in tagged[-1]["content"]
    plain = chat.create("Lenk: ugh", voice=True, glossary=False, tags=False)["messages"]
    assert "no tags in square brackets" in plain[0]["content"]
    assert "[sigh]" not in plain[0]["content"] and "lean toward" not in plain[-1]["content"]
