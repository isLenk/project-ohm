"""Tiny per-turn latency tracker for the voice pipeline.

start() when the user stops talking, mark() at each stage, and the [latency] line
prints once the bot starts playing. Only the first mark of each name counts.
"""
import time

import utils.trace as trace

_t0 = None
_marks = {}


def start(at=None):
    global _t0, _marks
    _t0 = time.perf_counter() if at is None else at
    _marks = {}


def mark(name):
    if _t0 is not None and name not in _marks:
        _marks[name] = time.perf_counter() - _t0
        trace.mark(name, _marks[name] * 1000)  # Pipeline tab


def report():
    if _t0 is None or not _marks:
        return
    print("[latency] speech end -> " + "  ".join(f"{n} +{t * 1000:.0f}ms" for n, t in _marks.items()))
