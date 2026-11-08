"""The opt-in cloud description of the screen (modules/vision/describe.py). Run from apps/engine:
    ../../.venv/Scripts/python.exe -m pytest tests -q
"""
import pytest
from PIL import Image

import controller.wrappers.openai as openai_module
from controller.wrappers.openai import OpenAIModel
from modules.vision import describe
from modules.vision.describe import DESCRIBE_EVERY_S, DESCRIBE_FRESH_S

LINE = "Allies lead 3-2 on Carentan, Lenk is redeploying."


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Memory:
    def __init__(self):
        self.logs = []

    def _background(self, fn, *args, **kwargs):
        fn(*args, **kwargs)  # inline, so the test sees the result

    def log(self, message, level="info", source="bot", data=None):
        self.logs.append((message, source, data))


@pytest.fixture
def setup(monkeypatch):
    clock, mem, tasks, grabs = Clock(), Memory(), [], []

    def grab(region=None, win=None):
        grabs.append(region)
        return Image.new("RGB", (2000, 1000))

    def run(task):
        tasks.append(task)
        return f'  "{LINE}" '

    monkeypatch.delenv("OHM_SCREEN_CLOUD", raising=False)
    monkeypatch.setattr(describe, "clock", clock)
    monkeypatch.setattr(describe, "memory", mem)
    monkeypatch.setattr(describe, "_latest", (0.0, ""))
    monkeypatch.setattr(describe, "_asked", None)
    monkeypatch.setattr(describe.screen, "on", lambda: True)
    monkeypatch.setattr(describe.screen, "grab", grab)
    monkeypatch.setattr(describe.window, "active", lambda: {"title": "Hell Let Loose - Google Chrome"})
    monkeypatch.setattr(describe.brain, "run", run)
    monkeypatch.setattr(describe.brain, "last_backend", lambda: "codex")
    return clock, mem, tasks, grabs


def test_off_by_default(setup):
    clock, mem, tasks, grabs = setup
    assert not describe.noticed((0, 0, 100, 100))
    assert tasks == [] and grabs == [] and describe.current() == ""


def test_on_describes_at_most_every_30_s(setup, monkeypatch):
    clock, mem, tasks, grabs = setup
    monkeypatch.setenv("OHM_SCREEN_CLOUD", "1")
    assert describe.noticed((10, 20, 500, 400))
    assert describe.current() == LINE
    task = tasks[0]
    assert task.role == "screen" and task.local_only is False and task.max_tokens == 80
    assert "Hell Let Loose (in Chrome)" in task.user and "25 words" in task.system
    assert len(task.images) == 1 and task.images[0].startswith("data:image/jpeg;base64,")
    assert grabs == [(10, 20, 500, 400)]
    message, source, data = mem.logs[0]
    assert (message, source) == (LINE, "screen")
    assert data["backend"] == "codex" and data["app"] == "Hell Let Loose (in Chrome)" and data["ms"] >= 0

    clock.now += DESCRIBE_EVERY_S - 1
    assert not describe.noticed() and len(tasks) == 1
    clock.now += 1
    assert describe.noticed() and len(tasks) == 2


def test_a_description_goes_stale(setup, monkeypatch):
    clock, mem, tasks, grabs = setup
    monkeypatch.setenv("OHM_SCREEN_CLOUD", "1")
    describe.noticed()
    clock.now += DESCRIBE_FRESH_S - 1
    assert describe.current() == LINE
    clock.now += 1
    assert describe.current() == ""


def test_no_answer_keeps_nothing(setup, monkeypatch):
    clock, mem, tasks, grabs = setup
    monkeypatch.setenv("OHM_SCREEN_CLOUD", "1")
    monkeypatch.setattr(describe.brain, "run", lambda task: None)
    describe.noticed()
    assert describe.current() == "" and mem.logs == []


def test_vision_off_means_off(setup, monkeypatch):
    clock, mem, tasks, grabs = setup
    monkeypatch.setenv("OHM_SCREEN_CLOUD", "1")
    monkeypatch.setattr(describe.screen, "on", lambda: False)
    assert not describe.noticed() and tasks == [] and grabs == []


class Client:
    def __init__(self):
        self.chat = self.completions = self

    def create(self, **kwargs):
        return kwargs


def test_prompt_lines(monkeypatch):
    m = OpenAIModel.__new__(OpenAIModel)
    m.name, m.model, m.system, m.client = "Ohm", "gemma", "You are Ohm.", Client()
    m.adapters = type("A", (), {"lora": lambda self, base=False: None})()
    monkeypatch.setattr(openai_module.mood, "line", lambda: "")
    seen = 'On their screen right now: Hell Let Loose. Text on it: "Allies 2 - 3 Axis"'

    def last(**kwargs):
        content = m.create("Lenk: who's winning", glossary=False, **kwargs)["messages"][-1]["content"]
        return content if isinstance(content, str) else content[0]["text"]

    monkeypatch.setattr(describe, "current", lambda: "")
    text = last(screen="data:image/jpeg;base64,AA", seen=seen)
    assert seen in text and "What's happening on their screen" not in text

    monkeypatch.setattr(describe, "current", lambda: "Axis leads 3-2 on Carentan.")
    text = last(screen="data:image/jpeg;base64,AA", seen=seen)
    assert seen in text and "What's happening on their screen: Axis leads 3-2 on Carentan." in text
    # Without a screenshot a fresh description still goes in; a utility call with its own system never gets it
    assert last() == "Lenk: who's winning\n\n(What's happening on their screen: Axis leads 3-2 on Carentan.)"
    assert last(system="Classify.") == "Lenk: who's winning"
