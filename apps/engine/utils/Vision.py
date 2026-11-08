"""Pictures people post in chat, ready to show the model.

Gemma 4 sees them through llama-server's vision projector (--mmproj in tools/_run_llama.cmd); they go in the
request as OpenAI-style image_url parts holding a data URL, so llama-server never has to fetch anything itself.
"""
import base64
import io

import discord

try:
    from PIL import Image
except ImportError:  # ? comes in through realtimetts; without it only formats llama-server reads itself are sent
    Image = None

MAX_IMAGES = 4                 # per message; each one costs a few hundred tokens of context
MAX_SIDE = 1024                # the projector downsizes anyway, this just keeps the request small
MAX_BYTES = 20 * 1024 * 1024
RAW_OK = {"image/png", "image/jpeg", "image/gif"}  # what llama-server decodes without Pillow (no webp)


async def message_images(message) -> list[str]:
    """Data URLs for the pictures attached to a Discord message (at most MAX_IMAGES)."""
    urls = []
    for a in message.attachments:
        if not (a.content_type or "").startswith("image/") or a.size > MAX_BYTES:
            continue
        try:
            data = await a.read()
        except discord.HTTPException as e:
            print(f"[vision] Couldn't download {a.filename}: {e}")
            continue
        url = data_url(data, a.content_type)
        if url:
            urls.append(url)
        if len(urls) >= MAX_IMAGES:
            break
    return urls


def data_url(data: bytes, content_type: str | None = None) -> str | None:
    """The image as a small JPEG data URL, or None if it can't be read."""
    if Image is None:
        if content_type not in RAW_OK:
            return None
        return f"data:{content_type};base64,{base64.b64encode(data).decode()}"
    try:
        img = Image.open(io.BytesIO(data))
        img.seek(0)  # first frame of a gif
        return image_url(img)
    except Exception as e:
        print(f"[vision] Couldn't read an image: {e}")
        return None


def image_url(img, max_side: int = MAX_SIDE) -> str:
    """A PIL image as a JPEG data URL, no bigger than max_side on its longest side."""
    if img.mode in ("RGBA", "LA", "P"):
        # Transparent parts would turn black; put it on white like Discord's light theme
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.getchannel("A"))
        img = bg
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def picture_note(text: str, count: int) -> str:
    """What someone said, plus a note that they sent pictures, so the saved conversation shows it too."""
    if not count:
        return text
    note = "(sent a picture)" if count == 1 else f"(sent {count} pictures)"
    return f"{text} {note}" if text else note
