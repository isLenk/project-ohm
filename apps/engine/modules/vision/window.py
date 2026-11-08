"""Which window is in front on this PC: its title, the program it belongs to, and where it is on the screen.

It costs nothing (a few Win32 calls, no screenshot), so it's what tells Ohm "Hell Let Loose" or "a YouTube video
in Chrome" even when the screenshot alone wouldn't, and it's where screen.capture() crops to.
"""
import ctypes
import os
from ctypes import wintypes

try:
    import psutil  # ? in the shared venv through other packages; without it there's just no program name
except ImportError:
    psutil = None

DWMWA_EXTENDED_FRAME_BOUNDS = 9  # the window as drawn, without the invisible resize border GetWindowRect counts
DPI_PER_MONITOR_AWARE_V2 = -4
SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
# A browser puts its own name after the page title; turned into "(in Chrome)" so the title reads like a phrase
BROWSERS = {" - Google Chrome": "Chrome", " - Microsoft Edge": "Edge",
            " — Mozilla Firefox": "Firefox", " - Mozilla Firefox": "Firefox", " - Brave": "Brave", " - Opera": "Opera"}
TITLE_MAX = 80  # a longer title is a page or file name nobody needs in full


def active() -> dict | None:
    """The foreground window as {"title", "process", "rect": (left, top, right, bottom)}, or None (no window,
    an empty title, not on Windows, or any Win32 call failing)."""
    if os.name != "nt":
        return None
    try:
        user32 = ctypes.windll.user32
        dpi_aware()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return {"title": title, "process": process_name(pid.value), "rect": window_rect(hwnd)}
    except Exception as e:
        print(f"[vision] Couldn't tell which window is in front: {e}")
        return None


def window_rect(hwnd) -> tuple[int, int, int, int] | None:
    """Where the window is on the screen in pixels, or None."""
    rect = wintypes.RECT()
    try:
        found = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)) == 0
    except Exception:
        found = False
    if not found and not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def screens() -> tuple[tuple[int, int, int, int], tuple[int, int]] | None:
    """(the bounds of every monitor together, (width, height) of the primary one) in pixels, or None."""
    if os.name != "nt":
        return None
    try:
        dpi_aware()
        m = ctypes.windll.user32.GetSystemMetrics
        left, top = m(SM_XVIRTUALSCREEN), m(SM_YVIRTUALSCREEN)
        return (left, top, left + m(SM_CXVIRTUALSCREEN), top + m(SM_CYVIRTUALSCREEN)), (m(SM_CXSCREEN), m(SM_CYSCREEN))
    except Exception:
        return None


def dpi_aware():
    """Real pixels from here on in this thread, the same ones ImageGrab uses, even with display scaling on."""
    try:
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(DPI_PER_MONITOR_AWARE_V2))
    except Exception:  # ? older than Windows 10 1607
        pass


def process_name(pid: int) -> str:
    """The program's exe name without ".exe" ("chrome", "HLL-Win64-Shipping"), or "" when it can't be told."""
    if psutil is None or not pid:
        return ""
    try:
        return psutil.Process(pid).name().removesuffix(".exe").removesuffix(".EXE")
    except Exception:  # ? gone already, or a protected process
        return ""


def describe(win: dict | None) -> str:
    """One short phrase for the prompt: the window's title, with a browser's suffix turned into "(in Chrome)".
    "" for no window."""
    # ? Edge puts a zero-width space in "Microsoft Edge", so the suffix wouldn't match
    title = " ".join(((win or {}).get("title") or "").replace("\u200b", "").split())
    for suffix, browser in BROWSERS.items():
        if title.endswith(suffix):
            page = title[: -len(suffix)].strip()
            return f"{shorten(page)} (in {browser})" if page else browser
    return shorten(title)


def shorten(text: str) -> str:
    return text if len(text) <= TITLE_MAX else text[: TITLE_MAX - 1].rstrip() + "…"
