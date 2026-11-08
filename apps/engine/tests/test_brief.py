"""The brief + extract job (controller/brief.py). Run from apps/engine:
    ../../.venv/Scripts/python.exe -m pytest tests -q
"""
import pytest

import controller.brief as brief
from controller.brief import BRIEF_EVERY_MESSAGES, BRIEF_EVERY_S, BRIEF_FRESH_S


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Memory:
    """What brief.py uses of the NAS client, writing to lists instead."""
    enabled = recall = True

    def __init__(self):
        self.stored, self.logs, self.extracted, self.known = [], [], [], []

    def _background(self, fn, *args, **kwargs):
        fn(*args, **kwargs)  # inline, so the test sees the result

    def search(self, character, text, limit=5):
        return [{"content": k, "similarity": 0.95} for k in self.known if k.lower() == text.lower()]

    def users(self):
        return [{"user_id": "2", "name": "Kyle"}]

    def add_memory(self, character, content, importance=0.5, user_ids=None, kind="event"):
        self.stored.append((content, user_ids, kind, importance))

    def log(self, message, level="info", source="bot", data=None):
        self.logs.append((message, source, data))

    def remember_if_relevant_async(self, model, character, speaker, text, user_ids=None):
        self.extracted.append((speaker, text, user_ids))


@pytest.fixture
def setup(monkeypatch):
    clock, mem, calls = Clock(), Memory(), []
    answer = {"value": {"summary": "Lenk and Kyle are  planning a game.",
                        "notes": [{"about": "Lenk", "note": "Lenk hates mornings."},
                                  {"about": "lenk", "note": "lenk hates mornings"},
                                  {"about": "Kyle", "note": "Kyle just started a new job."},
                                  {"about": "Lenk", "note": "Lenk plays guitar."}]}}
    monkeypatch.setattr(brief, "clock", clock)
    monkeypatch.setattr(brief, "memory", mem)
    monkeypatch.setattr(brief, "_pending", {})
    monkeypatch.setattr(brief, "_summaries", {})
    monkeypatch.setattr(brief, "_start_timer", lambda: None)
    monkeypatch.setattr(brief.brain, "run", lambda task: (calls.append(task), answer["value"])[1])
    return clock, mem, calls, answer


def say(n, channel=1):
    for i in range(n):
        brief.note(channel, "Lenk", f"line number {i}", "1", model="model", character="ohm")


def test_briefs_after_enough_lines(setup):
    clock, mem, calls, _ = setup
    say(BRIEF_EVERY_MESSAGES - 1)
    assert calls == []
    say(1)
    assert len(calls) == 1 and calls[0].role == "brief" and calls[0].schema == brief.SCHEMA
    assert calls[0].user.splitlines()[0] == "Lenk: line number 0"
    assert len(calls[0].user.splitlines()) == BRIEF_EVERY_MESSAGES
    assert brief._pending == {}  # the next line starts a new batch


def test_briefs_after_enough_time(setup):
    clock, mem, calls, _ = setup
    say(2)
    clock.now += BRIEF_EVERY_S - 1
    assert brief.check() == [] and calls == []
    clock.now += 1
    assert brief.check() == [1]  # what the timer does when nobody says anything more
    assert len(calls) == 1
    say(1, channel=2)
    clock.now += BRIEF_EVERY_S
    say(1, channel=2)  # a new line also checks
    assert len(calls) == 2 and calls[1].user.count("\n") == 1


def test_notes_stored_once_and_filed(setup):
    clock, mem, calls, _ = setup
    mem.known = ["Lenk plays guitar."]  # already on the NAS
    say(BRIEF_EVERY_MESSAGES)
    assert [s[0] for s in mem.stored] == ["Lenk hates mornings.", "Kyle just started a new job."]
    assert mem.stored[0][1] == ["1"]  # names nobody known, so under the speaker it's about
    assert mem.stored[1][1] == ["2"]  # names Kyle
    assert all(s[2] == "fact" and s[3] == 0.6 for s in mem.stored)
    message, source, data = mem.logs[-1]
    assert source == "brief" and message == f"Briefed #1: {BRIEF_EVERY_MESSAGES} lines → 2 notes"
    assert data["summary"] == "Lenk and Kyle are planning a game." and len(data["notes"]) == 3


def test_summary_goes_stale(setup):
    clock, mem, calls, _ = setup
    assert brief.current(1) == ""
    say(BRIEF_EVERY_MESSAGES)
    assert brief.current(1) == "Lenk and Kyle are planning a game."
    assert brief.current(2) == ""
    clock.now += BRIEF_FRESH_S
    assert brief.current(1) == ""


def test_falls_back_to_one_extract_per_line(setup):
    clock, mem, calls, answer = setup
    answer["value"] = None
    say(BRIEF_EVERY_MESSAGES)
    assert len(mem.extracted) == BRIEF_EVERY_MESSAGES
    assert mem.extracted[0] == ("Lenk", "line number 0", ["1"])
    assert mem.stored == [] and brief.current(1) == ""


def test_nothing_stored_with_memory_off(setup):
    clock, mem, calls, _ = setup
    mem.recall = False
    say(BRIEF_EVERY_MESSAGES)
    assert mem.stored == [] and brief.current(1) != ""
