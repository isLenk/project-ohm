"""Keep the LLM's stage directions to the ones Chatterbox can actually perform.

The model invents its own ("[quick, high-pitched gasp]", "*gasp*"), which the TTS reads out loud.
Bracketed or *starred* bits that mention a known sound become that tag, other [..] bits are dropped,
and *emphasis* keeps its words. Works on a stream, since a tag can be split across LLM chunks.
"""
import re

# Words the model uses for a sound/tone -> the Chatterbox tag that performs it
_WORDS = [
    (r"sigh", "[sigh]"),
    (r"chuckl", "[chuckle]"),
    (r"laugh|giggl|snicker|haha", "[laugh]"),
    (r"gasp", "[gasp]"),
    (r"groan|grumbl", "[groan]"),
    (r"throat", "[clear throat]"),
    (r"cough", "[cough]"),
    (r"sniff|sniffl", "[sniff]"),
    (r"shush|shh", "[shush]"),
    (r"whisper", "[whispering]"),
    (r"sarcas", "[sarcastic]"),
    (r"angr|annoyed", "[angry]"),
    (r"happ|cheerful", "[happy]"),
    (r"surpris|shock", "[surprised]"),
    (r"scared|fear|afraid", "[fear]"),
    (r"cry|cries|sob", "[crying]"),
]
# ? No \b: the model glues junk on ("*Fgasp*")
_WORDS = [(re.compile(w, re.IGNORECASE), tag) for w, tag in _WORDS]
_MAX_TAG_LEN = 80  # an "opened" bracket/star that runs longer than this wasn't a stage direction


def to_tag(inner: str) -> str | None:
    """The Chatterbox tag that a stage direction's text describes, or None."""
    for pattern, tag in _WORDS:
        if pattern.search(inner):
            return tag
    return None


class VoiceTagFilter:
    """Also drops the speaker label the model sometimes writes ("Ohm: ...") at the start of a line,
    since the TTS reads it out."""

    def __init__(self, name: str | None = None, max_tags: int = 1):
        self.label = f"{name}:".lower() if name else None
        self.max_tags = max_tags  # extra tags past this are dropped, so he can't pile them on
        self.tags = 0
        self.line = ""        # start of the current line, held back while it could still be "Ohm:"
        self.at_line_start = self.label is not None
        self.buf = ""         # an opened [.. or *.. we haven't seen the end of yet

    def feed(self, text: str) -> str:
        out = []
        for ch in text:
            if self.at_line_start:
                self.line += ch
                head = self.line.lstrip()
                if self.label.startswith(head.lower()):
                    if len(head) == len(self.label):  # it's the label: drop it
                        self.line, self.at_line_start = "", False
                    continue
                held, self.line, self.at_line_start = self.line, "", False
                out.append(self._tags(held))
            else:
                out.append(self._tags(ch))
                if ch == "\n" and self.label:
                    self.at_line_start = True
        return "".join(out)

    def _tags(self, text: str) -> str:
        out = []
        for ch in text:
            if self.buf:
                self.buf += ch
                closer = "]" if self.buf[0] == "[" else "*"
                if ch == closer:
                    out.append(self._resolve(self.buf))
                    self.buf = ""
                elif len(self.buf) > _MAX_TAG_LEN:
                    out.append(self.buf[1:])  # never closed: not a direction, keep the words
                    self.buf = ""
            elif ch in "[*":
                self.buf = ch
            else:
                out.append(ch)
        return "".join(out)

    def flush(self) -> str:
        """End of the reply: an unclosed [.. is dropped, an unclosed *.. keeps its words."""
        out = self._tags(self.line) if self.at_line_start and self.line.strip() else ""
        self.line = ""
        rest, self.buf = self.buf, ""
        return out + (rest[1:] if rest.startswith("*") else "")

    def _resolve(self, chunk: str) -> str:
        inner = chunk[1:-1]
        tag = to_tag(inner)
        if chunk[0] == "*" and not (tag and len(inner.split()) <= 4):
            return inner  # *...* that isn't a short action ("*gasp*", "*sighs heavily*") is emphasis
        if not tag or self.tags >= self.max_tags:
            return ""     # unknown [..], or one tag too many ("[gasp] *gasp*" -> one gasp)
        self.tags += 1
        return tag


def filter_voice_text(text: str, name: str | None = None) -> str:
    f = VoiceTagFilter(name)
    return f.feed(text) + f.flush()
