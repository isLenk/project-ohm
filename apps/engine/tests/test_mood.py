"""Ohm's mood (modules/mood) and the screen watcher's frame diff (modules/vision/watch.py). Run from apps/engine:
    ../../.venv/Scripts/python.exe -m pytest tests -q
"""
import pytest
from PIL import Image

import modules.mood.mood as mood_module
from modules.mood.mood import (ANNOYED_ON_INTERRUPT, ANNOYED_ON_SHUSH, DECAY_PER_MINUTE, HYPED_ON_LAUGH,
                               HYPED_ON_MENTION, LINE_THRESHOLD, Mood)
from modules.vision.watch import CHANGE_THRESHOLD, THUMB_SIZE, difference, thumbnail


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def logged(monkeypatch):
    """Mood on, and what it would have sent to the NAS log instead of sending it."""
    monkeypatch.setattr(mood_module.modules, "enabled", lambda name: True)
    sent = []
    monkeypatch.setattr(mood_module, "_log", lambda message, data: sent.append((message, data)))
    return sent


@pytest.fixture
def clock():
    return Clock()


def test_bumps(logged, clock):
    m = Mood(clock=clock)
    m.heard("lmao that's so bad", mentioned=True)
    assert m.hyped == pytest.approx(HYPED_ON_MENTION + HYPED_ON_LAUGH)
    m.interrupted()
    m.shushed()
    assert m.annoyed == pytest.approx(ANNOYED_ON_INTERRUPT + ANNOYED_ON_SHUSH)


def test_what_counts_as_laughing():
    for text in ["lol", "LMAOOO", "hahaha ok", "😂"]:
        assert mood_module.LAUGHTER.search(text), text
    for text in ["lollipop", "what", "hal is here"]:
        assert not mood_module.LAUGHTER.search(text), text


def test_plain_talk_does_not_hype(logged, clock):
    m = Mood(clock=clock)
    m.heard("anyone up for a game")
    assert m.hyped == 0


def test_feelings_fade_over_time(logged, clock):
    m = Mood(clock=clock)
    m.shushed()
    clock.now += 60
    m.spoke()
    assert m.annoyed == pytest.approx(ANNOYED_ON_SHUSH - DECAY_PER_MINUTE)
    clock.now += 60 * 60
    m.spoke()
    assert m.annoyed == 0


def test_feelings_stay_between_0_and_1(logged, clock):
    m = Mood(clock=clock)
    for _ in range(20):
        m.shushed()
    assert m.annoyed == 1
    m.spoke()
    assert m.bored == 0


def test_silence_makes_him_bored(logged, clock):
    m = Mood(clock=clock)
    for _ in range(10 * 60):  # ten minutes of idle-loop ticks
        clock.now += 1
        m.quiet(30)
    assert m.bored > LINE_THRESHOLD
    assert "bored" in m.line()


def test_only_the_quiet_part_counts(logged, clock):
    m = Mood(clock=clock)
    clock.now += 600
    m.quiet(1)  # ten minutes since the last event, but only the last second was quiet
    assert m.bored < 0.01


def test_line_says_nothing_under_the_threshold(logged, clock):
    m = Mood(clock=clock)
    assert m.line() == "" and m.tag_hint() == ""
    m.annoyed = LINE_THRESHOLD - 0.01
    assert m.line() == ""
    m.annoyed = LINE_THRESHOLD
    assert m.line() == "You're a little annoyed at being talked over."
    assert m.tag_hint() == "If a sound fits, lean toward [groan]."
    m.annoyed, m.hyped = 0.2, 0.9
    assert m.line() == "You're hyped up right now."
    assert "[laugh]" in m.tag_hint()


def test_idle_chance_goes_up_with_boredom_and_down_when_annoyed(logged, clock):
    m = Mood(clock=clock)
    chances = []
    for bored in [0, 0.25, 0.5, 0.75, 1]:
        m.bored = bored
        chances.append(m.idle_chance(0.5))
    assert chances == sorted(chances)
    assert chances[0] == 0.5 and chances[-1] == 1.0
    m.bored, m.annoyed = 0, 1
    assert m.idle_chance(0.5) < 0.5


def test_crossing_the_threshold_is_logged_once_each_way(logged, clock):
    m = Mood(name="Ohm", clock=clock)
    m.shushed()
    m.shushed()
    m.interrupted()  # still annoyed: nothing new to log
    assert [msg for msg, _ in logged] == ["Ohm is annoyed now (0.70)"]
    clock.now += 60 * 60
    m.spoke()
    assert logged[-1][0] == "Ohm isn't annoyed anymore (0.00)"
    assert logged[0][1]["dominant"] == "annoyed"


def test_module_off_does_nothing(monkeypatch, clock):
    monkeypatch.setattr(mood_module.modules, "enabled", lambda name: False)
    m = Mood(clock=clock)
    m.shushed()
    m.heard("lol ohm", mentioned=True)
    assert (m.bored, m.hyped, m.annoyed) == (0, 0, 0)
    m.bored = 1
    assert m.line() == "" and m.tag_hint() == ""
    assert m.idle_chance(0.5) == 0.5


def test_thumbnail_diff_sees_a_new_screen_not_a_cursor():
    dark = Image.new("RGB", (1920, 1080), (20, 20, 20))
    cursor = dark.copy()
    cursor.paste((255, 255, 255), (900, 500, 916, 524))  # a mouse pointer's worth of change
    bright = Image.new("RGB", (1920, 1080), (200, 200, 200))
    a, b, c = thumbnail(dark), thumbnail(cursor), thumbnail(bright)
    assert a.size == THUMB_SIZE and a.mode == "L"
    assert difference(a, a) == 0
    assert difference(a, b) < CHANGE_THRESHOLD
    assert difference(a, c) > CHANGE_THRESHOLD
