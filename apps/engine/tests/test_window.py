"""The phrase for the window in front (modules/vision/window.py). Run from apps/engine:
    ../../.venv/Scripts/python.exe -m pytest tests -q
"""
from modules.vision.window import TITLE_MAX, describe


def test_a_game_is_its_title():
    win = {"title": "Hell Let Loose", "process": "HLL-Win64-Shipping", "rect": (0, 0, 1920, 1080)}
    assert describe(win) == "Hell Let Loose"


def test_a_browser_suffix_turns_into_in_browser():
    assert describe({"title": "lofi beats - YouTube - Google Chrome"}) == "lofi beats - YouTube (in Chrome)"
    assert describe({"title": "Docs - Microsoft​ Edge"}) == "Docs (in Edge)"
    assert describe({"title": "Reddit — Mozilla Firefox"}) == "Reddit (in Firefox)"
    assert describe({"title": "New Tab - Google Chrome"}) == "New Tab (in Chrome)"


def test_no_window_and_long_titles():
    assert describe(None) == "" and describe({"title": ""}) == ""
    long = describe({"title": "x" * 200})
    assert len(long) == TITLE_MAX and long.endswith("…")
    assert describe({"title": "  Visual   Studio\tCode "}) == "Visual Studio Code"
