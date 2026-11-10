"""Masks names and contact details before a cloud call and puts them back in the answer.
Only registered people are caught; images go as they are."""

import random
import re
import threading
import time
from typing import Callable, Iterable

PEOPLE_TTL = 30  # seconds before the sources are asked again
MIN_NAME = 2

# Stand-ins. Not English words, so reveal() can't rewrite ordinary text
POOL = ("Marcus Theo Priya Jonas Elena Tobias Nadia Felix Iris Omar Leila Ravi Sofia Dmitri Yuki Mateo Ingrid Kofi "
        "Anika Lucas Mira Silas Zara Hugo Talia Elias Noor Caleb Freya Idris Amara Bruno Celine Dario Esme Farah "
        "Gideon Hana Ivo Jasper Kenji Lena Malik Nico Odette Pavel Soren Tess Umar Vera Xavier Yara Zane Anton "
        "Bianca Cyrus Delia Emil Fiona Gael Helena Imani Joel Keira Leon Maya Nils Olga Petra Rafael Sanna Tomas "
        "Viktor Willa Ximena Yusuf Zoltan Aditi Bastian Corin Dagny Eamon Fenna Galen Hollis Ilse Jorah Kasia").split()

# ? Order matters: links hold emails, phone would eat ids
PATTERNS = [
    ("link", re.compile(r"(?:https?://|www\.|discord\.gg/)\S+", re.IGNORECASE)),
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("id", re.compile(r"(?<!\w)\d{17,20}(?!\w)")),
    ("tag", re.compile(r"(?<![\w#])[\w.]{2,32}#\d{4}(?!\w)")),  # old Discord tag, name#1234
    ("ip", re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")),
    ("phone", re.compile(r"(?<![\w+])\+?\d[\d\s().-]{7,}\d(?!\w)")),
]
PLACEHOLDER = re.compile(r"\[(link|email|id|tag|ip|phone)-(\d+)\]")

_sources: list[Callable[[], Iterable[Iterable[str]]]] = []
_cache: tuple[float, list[list[str]]] = (0.0, [])
_lock = threading.Lock()


def add_people(source: Callable[[], Iterable[Iterable[str]]]) -> None:
    """Register a source of people, each a list of names (shown name first). Re-read every PEOPLE_TTL"""
    global _cache
    with _lock:
        _sources.append(source)
        _cache = (0.0, [])


def people() -> list[list[str]]:
    """Everyone every source knows"""
    global _cache
    with _lock:
        at, cached = _cache
        if at and time.monotonic() - at < PEOPLE_TTL:
            return cached
        out = []
        for source in _sources:
            try:
                out += [clean(p) for p in source() or []]
            except Exception as e:
                print(f"[brain] couldn't read a name list: {e}")
        out = [p for p in out if p]
        _cache = (time.monotonic(), out)
        return out


def clean(names: Iterable[str]) -> list[str]:
    out = []
    for n in names or []:
        n = " ".join(str(n or "").split()).lstrip("@")
        if len(n) >= MIN_NAME and n.lower() not in (o.lower() for o in out):
            out.append(n)
    return out


def _words(names: Iterable[str]) -> re.Pattern | None:
    names = sorted({n for n in names}, key=len, reverse=True)
    if not names:
        return None
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(n) for n in names) + r")(?!\w)", re.IGNORECASE)


class Mask:
    def __init__(self, everyone: Iterable[Iterable[str]] = (), rng: random.Random | None = None):
        rng = rng or random.SystemRandom()
        groups = [clean(p) for p in everyone]
        groups = [g for g in groups if g]
        taken = {n.lower() for g in groups for n in g}
        pool = [n for n in POOL if n.lower() not in taken]
        rng.shuffle(pool)
        self.stand_in = {}   # real name -> stand-in
        self.real = {}       # stand-in -> first name
        merged = {}
        for g in groups:     # same person from two sources, one stand-in
            key = next((merged[n.lower()] for n in g if n.lower() in merged), None)
            if key is None:
                key = pool.pop() if pool else f"Person{len(self.real) + 1}"
                self.real[key.lower()] = g[0]
            for n in g:
                merged.setdefault(n.lower(), key)
        self.stand_in = merged
        self._names = _words(self.stand_in)
        self._back = _words(self.real)
        self.originals = {}  # placeholder -> original
        self._seen = {}      # original -> placeholder

    @property
    def knows_anyone(self) -> bool:
        return bool(self.stand_in)

    def hide(self, text: str) -> str:
        if not text:
            return text
        for kind, rx in PATTERNS:
            text = rx.sub(lambda m, k=kind: self._placeholder(k, m.group(0)), text)
        if self._names:
            text = self._names.sub(lambda m: self.stand_in[m.group(1).lower()], text)
        return text

    def _placeholder(self, kind: str, original: str) -> str:
        if kind == "phone" and sum(c.isdigit() for c in original) < 9:
            return original  # a score or date range
        if original not in self._seen:
            n = sum(1 for p in self.originals if p.startswith(f"[{kind}-")) + 1
            self._seen[original] = f"[{kind}-{n}]"
            self.originals[self._seen[original]] = original
        return self._seen[original]

    def reveal(self, text):
        if not isinstance(text, str) or not text:
            return text
        if self._back:
            text = self._back.sub(lambda m: self.real[m.group(1).lower()], text)
        return PLACEHOLDER.sub(lambda m: self.originals.get(m.group(0), m.group(0)), text)

    def reveal_value(self, value):
        """reveal() every string in parsed JSON, keys too"""
        if isinstance(value, str):
            return self.reveal(value)
        if isinstance(value, list):
            return [self.reveal_value(v) for v in value]
        if isinstance(value, dict):
            return {self.reveal(k): self.reveal_value(v) for k, v in value.items()}
        return value
