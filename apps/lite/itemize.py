"""/itemize: list what's in a photo via the `itemize` brain role"""

import base64
import io
import os

from PIL import Image, ImageOps

import brain
from brain import Task

os.environ.setdefault("BRAIN_ROLE_ITEMIZE", "codex,openrouter")

MAX_SIDE = 1600        # px; small print still readable
SECONDS = 180          # model timeout

SYSTEM = """You list what's in a photo. Most photos are receipts or bills; some are a shelf, a table or a cart.

For a receipt or bill: one item per line as printed, with its quantity and the line's total price. Copy the
subtotal, tax and total as printed; don't work them out yourself. Use null for a number that isn't on it.
For any other photo: one item per distinct thing you can see, with how many, and null prices.

Keep names short and readable (expand cryptic receipt abbreviations when you're sure). `title` is the shop or a
few words for what the photo shows. `date` is YYYY-MM-DD or "". `currency` is a symbol like $ or "".
`note` answers anything the person asked, or says what you couldn't read; "" when there's nothing to say."""

NUMBER = {"type": ["number", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "date": {"type": "string"},
        "currency": {"type": "string"},
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "qty": {"type": "number"}, "price": NUMBER}}},
        "subtotal": NUMBER,
        "tax": NUMBER,
        "total": NUMBER,
        "note": {"type": "string"},
    },
    "required": ["items"],
}


def shrink(data: bytes) -> bytes:
    """Upright JPEG within MAX_SIDE; raises if not an image"""
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def read(jpeg: bytes, note: str = ""):
    """Dict shaped like SCHEMA, or None if no backend answered"""
    user = "Itemize this photo." + (f"\n\nThe person adds: {note}" if note else "")
    picture = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    # ? Only the note can hold names; non-private still works with the NAS down
    return brain.run(Task("itemize", SYSTEM, user, schema=SCHEMA, images=[picture], max_tokens=2000,
                          reasoning="low", private=bool(note), timeout=SECONDS))


def mismatch(found: dict, money) -> str:
    """Warning if items don't sum to the receipt, else empty"""
    prices = [i.get("price") for i in found.get("items") or []]
    target = found.get("subtotal") if found.get("subtotal") is not None else found.get("total")
    if target is None or not prices or any(p is None for p in prices):
        return ""
    # Against the total, allow for tax
    tax = (found.get("tax") or 0) if found.get("subtotal") is None else 0
    added = sum(prices)
    if abs(added + tax - target) < 0.01 or abs(added - target) < 0.01:
        return ""
    return f"The items add up to {money(added)}, but the receipt says {money(target)}. Check the photo."
