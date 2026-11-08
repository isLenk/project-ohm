"""Ohm's brain: one-shot LLM tasks (prompt in, text or JSON out) sent to whichever backend each role is set to.

    from brain import Task, run
    note = run(Task("extract", system, f"{speaker}: {text}", max_tokens=40))

See README.md for the env vars, backends and the batching pattern.
"""

from .router import last_backend, needs_gpu_swap, pack, run, run_many, run_packed, unpack
from .status import status
from .task import Task

__all__ = ["Task", "run", "run_many", "run_packed", "pack", "unpack", "needs_gpu_swap", "status", "last_backend"]
