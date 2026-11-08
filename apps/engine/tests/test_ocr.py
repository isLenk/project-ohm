"""Which lines of OCR text make it into the prompt (modules/vision/ocr.py), with the recognizer faked.
Run from apps/engine:
    ../../.venv/Scripts/python.exe -m pytest tests -q
"""
from PIL import Image

from modules.vision import ocr
from modules.vision.ocr import OCR_LINES, keep


def test_keeps_real_lines_in_order():
    lines = ["  Allies   2 - 3 Axis ", "OK", "12:45", "x", "Deploy", "deploy", "DEPLOY", "vee,-.~oso", ""]
    assert keep(lines) == ["Allies 2 - 3 Axis", "Deploy"]


def test_at_most_ocr_lines():
    assert keep([f"line {i}" for i in range(30)]) == [f"line {i}" for i in range(OCR_LINES)]


def test_text_uses_the_recognizer_and_fails_soft(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "recognize", lambda image, max_side: ["Main Menu", "!!", "Main menu", "Settings"])
    assert ocr.text(Image.new("RGB", (4, 4))) == ["Main Menu", "Settings"]

    def broken(image, max_side):
        raise OSError("no OCR today")
    monkeypatch.setattr(ocr, "recognize", broken)
    assert ocr.text(Image.new("RGB", (4, 4))) == []
    monkeypatch.setattr(ocr, "available", lambda: False)
    assert ocr.text(Image.new("RGB", (4, 4))) == []
