"""The text on a screenshot, read by Windows' own OCR (Windows.Media.Ocr), so Ohm can quote a scoreboard, a menu
or a chat message the small vision model would squint at.

It runs on this PC, on the screenshot at up to OCR_SIDE (small text is lost at the 1600px the model gets): about
160 ms for a whole 3440x1440 screen, 50 ms at 1600px, less for a cropped window. It needs the winrt packages (in
pyproject.toml, Windows only); without them text() is just empty.
"""
import asyncio
import re

try:
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
except ImportError:  # not on Windows, or the winrt packages aren't installed
    OcrEngine = None

OCR_LINES = 10      # the most lines that go in the prompt; a full page of text is the screenshot's job
OCR_SIDE = 2560     # read at up to this on the longest side: 1600px misreads HUD text, full 3440px takes ~270 ms
MIN_CHARS = 3       # "OK", "x" and lone icons read as letters are noise
CLEAN_SHARE = 0.7   # a line with fewer letters and digits than this (",-.~oso") is OCR misreading a picture
LETTER = re.compile(r"[^\W\d_]")

_engine = None


def available() -> bool:
    return OcrEngine is not None and engine() is not None


def health() -> tuple[bool, str]:
    if OcrEngine is None:
        return False, "The winrt packages aren't installed, so text on the screen isn't read"
    if engine() is None:
        return False, "Windows has no OCR language installed (Settings › Time & language › Language)"
    return True, "Reads the text on the screen with Windows' OCR"


def engine():
    """Windows' OCR in the user's own languages, made once, or None."""
    global _engine
    if _engine is None and OcrEngine is not None:
        try:
            _engine = OcrEngine.try_create_from_user_profile_languages()
        except Exception as e:
            print(f"[vision] Windows OCR isn't available: {e}")
    return _engine


def text(image, max_side=OCR_SIDE) -> list[str]:
    """The lines of text on a PIL image, top to bottom, cleaned up by keep() (at most OCR_LINES).
    [] when OCR isn't available or fails. Blocking, run it in a thread."""
    if not available():
        return []
    try:
        return keep(recognize(image, max_side))
    except Exception as e:
        print(f"[vision] Couldn't read the text on the screen: {e}")
        return []


def recognize(image, max_side=OCR_SIDE) -> list[str]:
    """Every line Windows' OCR finds on `image` (shrunk to max_side), in its order (top to bottom)."""
    image = image.convert("RGBA")  # ? a copy, so the caller's image can be encoded at the same time
    image.thumbnail((max_side, max_side))
    writer = DataWriter()
    writer.write_bytes(image.tobytes("raw", "BGRA"))
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                    image.width, image.height)

    async def read():
        return await engine().recognize_async(bitmap)

    # ? A fresh event loop each time: this runs in a worker thread, never on the bot's own loop
    return [line.text for line in asyncio.run(read()).lines]


def keep(lines) -> list[str]:
    """The lines worth showing: trimmed, each once (any case), at least MIN_CHARS long, with letters in them and
    mostly letters and digits (CLEAN_SHARE), in their order, at most OCR_LINES."""
    out, seen = [], set()
    for line in lines:
        line = " ".join(str(line or "").split())
        if len(line) < MIN_CHARS or not LETTER.search(line) or line.lower() in seen:
            continue
        chars = line.replace(" ", "")
        if sum(c.isalnum() for c in chars) < CLEAN_SHARE * len(chars):
            continue
        seen.add(line.lower())
        out.append(line)
        if len(out) >= OCR_LINES:
            break
    return out
