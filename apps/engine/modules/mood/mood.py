"""How Ohm feels right now: bored, hyped and annoyed, each 0..1 (the mood module, on by default).

A tiny state machine, no LLM. Things that happen in the call bump a feeling up, and every feeling fades on its own
over time. The result is one short line in his prompt, a nudge on how often he talks on his own when it's quiet,
and a hint at which voice tag fits.
"""
import re
import threading
import time

from controller import modules
from controller.memory import memory

DECAY_PER_MINUTE = 0.15        # feelings fade: a full 1.0 is gone after ~7 minutes of nothing happening
BORED_PER_MINUTE_QUIET = 0.3   # silence raises bored (minus the decay that's ~0.15/min, so ~3 minutes to "a bit bored")
BORED_DROP_ON_HEARD = 0.05     # people talking is something happening
BORED_DROP_ON_SPOKE = 0.1      # talking himself lowers it a bit too
HYPED_ON_MENTION = 0.2         # someone said his name
HYPED_ON_LAUGH = 0.15          # someone laughed
ANNOYED_ON_INTERRUPT = 0.2     # got talked over mid-sentence
ANNOYED_ON_SHUSH = 0.35        # got told to shut up
LINE_THRESHOLD = 0.5           # below it the prompt says nothing about how he feels
STRONG = 0.8                   # above it the line says it stronger
IDLE_ANNOYED_CUT = 0.8         # fully annoyed, he's 80% less likely to pipe up on his own
LAUGHTER = re.compile(r"(?<!\w)(?:lo+l|lmf?ao+|rofl|ha(?:ha)+h?|he(?:he)+)(?!\w)|😂|🤣", re.IGNORECASE)

FEELINGS = ("bored", "hyped", "annoyed")
LINES = {
    "bored": ("Right now you're a bit bored and would rather something happened.",
              "Right now you're really bored and want something, anything, to happen."),
    "hyped": ("You're in a good mood right now and up for a laugh.",
              "You're hyped up right now."),
    "annoyed": ("You're a little annoyed at being talked over.",
                "You're annoyed right now: people keep talking over you."),
}
TAGS = {"bored": "[sigh]", "hyped": "[laugh] or [chuckle]", "annoyed": "[groan]"}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


class Mood:
    def __init__(self, name: str = "Ohm", clock=time.monotonic):
        self.name = name  # who's feeling it, for the log lines
        self.clock = clock
        self.bored = self.hyped = self.annoyed = 0.0
        self.updated_at = clock()
        self._dominant = None  # the feeling over LINE_THRESHOLD last time we looked, see _check
        self._lock = threading.Lock()  # ? heard() comes from the speech-to-text thread, the rest from the event loop

    @staticmethod
    def on() -> bool:
        return modules.enabled("mood")

    # ── Events ─────────────────────────────────────────────────────────────
    def _apply(self, **bumps):
        """Fade every feeling for the time since the last event, then add `bumps` ({feeling: amount})."""
        if not self.on():
            return
        with self._lock:
            now = self.clock()
            fade = DECAY_PER_MINUTE * max(0.0, now - self.updated_at) / 60
            for f in FEELINGS:
                setattr(self, f, _clamp(getattr(self, f) - fade + bumps.get(f, 0.0)))
            self.updated_at = now
        self._check()

    def quiet(self, seconds: float):
        """A tick of the idle loop while nobody is talking. seconds: how long the call has been quiet."""
        # ? Only the part of the time since the last event that was actually quiet counts
        quiet_for = min(max(0.0, self.clock() - self.updated_at), max(0.0, seconds))
        self._apply(bored=BORED_PER_MINUTE_QUIET * quiet_for / 60)

    def heard(self, text: str, mentioned: bool = False):
        """Someone said `text`. mentioned: it has his name in it."""
        hyped = (HYPED_ON_MENTION if mentioned else 0.0) + (HYPED_ON_LAUGH if LAUGHTER.search(text or "") else 0.0)
        self._apply(hyped=hyped, bored=-BORED_DROP_ON_HEARD)

    def interrupted(self):
        self._apply(annoyed=ANNOYED_ON_INTERRUPT)

    def shushed(self):
        self._apply(annoyed=ANNOYED_ON_SHUSH)

    def spoke(self):
        self._apply(bored=-BORED_DROP_ON_SPOKE)

    # ── Outputs ────────────────────────────────────────────────────────────
    def dominant(self) -> str | None:
        """The strongest feeling if it's at least LINE_THRESHOLD, else None."""
        if not self.on():
            return None
        f = max(FEELINGS, key=lambda f: getattr(self, f))
        return f if getattr(self, f) >= LINE_THRESHOLD else None

    def line(self) -> str:
        """One short sentence for his prompt about how he feels, or "" when he feels nothing much."""
        f = self.dominant()
        if f is None:
            return ""
        return LINES[f][getattr(self, f) >= STRONG]

    def tag_hint(self) -> str:
        """One sentence nudging him toward the voice tag that fits the mood, or "". Voice replies only."""
        f = self.dominant()
        return f"If a sound fits, lean toward {TAGS[f]}." if f else ""

    def idle_chance(self, base: float) -> float:
        """How likely he is to talk on his own in a quiet stretch: up to 2x `base` when bored, less when annoyed."""
        if not self.on():
            return base
        return _clamp(base * (1 + self.bored) * (1 - IDLE_ANNOYED_CUT * self.annoyed))

    def snapshot(self) -> dict:
        return {"bored": round(self.bored, 2), "hyped": round(self.hyped, 2), "annoyed": round(self.annoyed, 2),
                "dominant": self.dominant()}

    def health(self) -> tuple[bool, str]:
        s = self.snapshot()
        return True, f"Feeling {s['dominant'] or 'fine'} (bored {s['bored']}, hyped {s['hyped']}, annoyed {s['annoyed']})"

    def _check(self):
        """Log it when the strongest feeling crosses LINE_THRESHOLD, up or down."""
        now = self.dominant()
        was, self._dominant = self._dominant, now
        if now == was:
            return
        if now:
            message = f"{self.name} is {now} now ({getattr(self, now):.2f})"
        else:
            message = f"{self.name} isn't {was} anymore ({getattr(self, was):.2f})"
        print(f"[mood] {message}")
        _log(message, self.snapshot())


def _log(message: str, data: dict):
    """To the NAS log (the dashboard's Logs tab), off the calling thread."""
    if memory.enabled:
        memory._background(memory.log, message, source="mood", data=data)


mood = Mood()
