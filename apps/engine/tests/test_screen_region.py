"""Which part of the screen a screenshot shows (modules/vision/screen.py and watch.changed_region).
Run from apps/engine:
    ../../.venv/Scripts/python.exe -m pytest tests -q
"""
from PIL import Image, ImageDraw

from modules.vision import screen, watch
from modules.vision.watch import REGION_MAX, THUMB_SIZE

SCREEN = (3200, 1800)                       # 50x the 64x36 thumbnail on each side
SCREENS = ((-1920, 0, 3200, 1800), SCREEN)  # a second monitor to the left of the primary one


def thumbs(box):
    """Two thumbnails, the second with `box` (in thumbnail pixels, inclusive) painted white."""
    a = Image.new("L", THUMB_SIZE, 30)
    b = a.copy()
    ImageDraw.Draw(b).rectangle(box, fill=255)
    return a, b


def test_a_small_change_is_padded_and_scaled_up():
    a, b = thumbs((10, 5, 19, 14))  # a 10x10 block: getbbox gives (10, 5, 20, 15)
    assert watch.changed_region(a, b, SCREEN) == (450, 200, 1050, 800)  # 1 px of padding each side, times 50


def test_the_crop_is_clamped_to_the_screen():
    a, b = thumbs((0, 0, 9, 9))
    assert watch.changed_region(a, b, SCREEN) == (0, 0, 550, 550)
    a, b = thumbs((54, 26, 63, 35))
    assert watch.changed_region(a, b, SCREEN)[2:] == SCREEN


def test_no_change_or_a_big_one_means_the_default():
    a, _ = thumbs((0, 0, 0, 0))
    assert watch.changed_region(a, a.copy(), SCREEN) is None
    a, b = thumbs((0, 0, 50, 30))
    assert (51 * 31) / (64 * 36) > REGION_MAX and watch.changed_region(a, b, SCREEN) is None
    a, b = thumbs((10, 5, 19, 14))
    assert watch.changed_region(a, b, None) is None  # screen size unknown
    faint = Image.new("L", THUMB_SIZE, 30 + watch.PIXEL_THRESHOLD)  # changed, but not by more than the threshold
    assert watch.changed_region(a, faint, SCREEN) is None


def test_clamp():
    assert screen.clamp((-2500, -10, 500, 900), SCREENS[0]) == (-1920, 0, 500, 900)
    assert screen.clamp((-32000, -32000, -31800, -31900), SCREENS[0]) is None  # a minimized window
    assert screen.clamp((100, 100, 120, 500), SCREENS[0]) is None  # too thin to show anything
    assert screen.clamp(None, SCREENS[0]) is None and screen.clamp((0, 0, 500, 500), None) is None


def test_window_region_only_when_it_doesnt_fill_the_screen():
    assert screen.window_region({"rect": (100, 50, 1700, 950)}, SCREENS) == (100, 50, 1700, 950)
    assert screen.window_region({"rect": (-1900, 10, -100, 1000)}, SCREENS) == (-1900, 10, -100, 1000)
    assert screen.window_region({"rect": (0, 0, 3200, 1800)}, SCREENS) is None  # full screen
    assert screen.window_region({"rect": (-8, -8, 3208, 1808)}, SCREENS) is None  # maximized
    assert screen.window_region(None, SCREENS) is None
    assert screen.window_region({"rect": (100, 50, 1700, 950)}, None) is None


def test_grab_uses_the_region_then_the_window(monkeypatch):
    grabs = []

    class Grab:
        @staticmethod
        def grab(bbox=None, all_screens=False):
            grabs.append((bbox, all_screens))
            return Image.new("RGB", (10, 10))

    monkeypatch.setattr(screen, "ImageGrab", Grab)
    monkeypatch.setattr(screen, "on", lambda: True)
    monkeypatch.setattr(screen.window, "screens", lambda: SCREENS)
    monkeypatch.setattr(screen.window, "active", lambda: {"rect": (100, 50, 1700, 950)})
    screen.grab((450, 200, 1050, 800))
    screen.grab()
    screen.grab((-1500, 0, -400, 1800))  # on the second monitor, the only time every screen is grabbed
    monkeypatch.setattr(screen.window, "active", lambda: None)
    screen.grab()
    assert grabs == [((450, 200, 1050, 800), False), ((100, 50, 1700, 950), False),
                     ((-1500, 0, -400, 1800), True), (None, False)]


def test_seen_line():
    assert screen.seen_line("Hell Let Loose", ["Allies 2 - 3 Axis", "Deploy"]) == \
        'On their screen right now: Hell Let Loose. Text on it: "Allies 2 - 3 Axis" · "Deploy"'
    assert screen.seen_line("Hell Let Loose", []) == "On their screen right now: Hell Let Loose."
    assert screen.seen_line("", ["Deploy"]) == 'Text on their screen right now: "Deploy"'
    assert screen.seen_line("", []) == ""
    assert len(screen.seen_line("x", ["a" * 100] * 10)) < screen.TEXT_MAX + 60
