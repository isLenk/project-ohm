"""The judge reads each reply the adapter would train on and says whether it answers the line before it.

The export lays every Ohm turn out after the message its reply arrow points at (export.py), but in a busy
group chat about a third of real replies still don't answer that message: the arrow was guessed, or the
person was carrying on their own thread. An adapter trained on those learns to ignore what was just said.
Verdicts are kept in the project (reply_checks), so a reply is only ever checked once, and the export
leaves out the ones that don't answer (Documentation/Curator/training.md, "Checking replies with the judge").

The judge is the brain's `judge` role, 30 replies to a call. Ohm's llama-server only stops for it when that's
the local Qwen model (brain.needs_gpu_swap).
"""

import brain

from .. import export, gpu
from ..errors import ApiError
from ..project import now_ms
from . import evaluate

INSTRUCTIONS = (f"{evaluate.SEPARATE} Each has one candidate, A. For each item, \"answers\" is true when A "
                "answers the last message of its chat.")
ITEM = {"type": "object", "properties": {"answers": {"type": "boolean"}}, "required": ["answers"]}


def pending(project) -> list[dict]:
    """Trained turns the judge hasn't seen yet: {key, context, reply}, each once."""
    data = export.build(project)
    with project.lock:
        done = {r[0] for r in project.conn.execute("SELECT reply_ids FROM reply_checks")}
    out, seen = [], set()
    for e in data["examples"]:
        turn = 0
        for i, m in enumerate(e["messages"]):
            if m["role"] != "assistant":
                continue
            key = export.reply_key(e["turn_ids"][turn])
            turn += 1
            if key in done or key in seen:
                continue
            seen.add(key)
            out.append({"key": key, "context": e["messages"][:i], "reply": m["content"]})
    return out


def prepare(project, reg) -> list[dict]:
    """What to check, after making sure the judge can run."""
    evaluate.check_judge()
    if reg.one("SELECT id FROM train_runs WHERE status IN ('queued','running')"):
        raise ApiError(409, "run_active", "Wait for the training run to finish: they'd share the GPU.")
    return pending(project)


def prompt(it: dict) -> str:
    return evaluate.judge_prompt(evaluate.transcript(it["context"]), [("A", it["reply"])])


def run(project, reg, todo: list[dict], job) -> dict:
    """Check every reply in `todo`, saving each round's verdicts as they come."""
    counts = {"answers": 0, "does_not": 0, "unreadable": 0}
    if not todo:
        return counts
    judge = evaluate.judge_name() or "unknown"
    swap = brain.needs_gpu_swap("judge")
    before = gpu.running_llama() if swap else None
    judge_pid = None
    step = evaluate.PACK_SIZE * evaluate.PACK_WORKERS
    try:
        job.progress(0, len(todo), "loading the judge model" if swap else "asking the judge")
        if swap:
            if before:
                gpu.stop([before["pid"]])
            judge_pid = evaluate._start_judge()
        for start in range(0, len(todo), step):
            if job.cancelled:
                break
            batch = todo[start:start + step]
            verdicts = evaluate.ask_packed([prompt(it) for it in batch], evaluate.JUDGE_RULES, INSTRUCTIONS, ITEM)
            with project.tx() as conn:
                for it, v in zip(batch, verdicts):
                    if not isinstance((v or {}).get("answers"), bool):
                        counts["unreadable"] += 1  # not stored, so the next check asks again
                        continue
                    conn.execute("INSERT OR REPLACE INTO reply_checks (reply_ids, answers, judge, checked_at) "
                                 "VALUES (?, ?, ?, ?)", (it["key"], int(v["answers"]), judge, now_ms()))
                    counts["answers" if v["answers"] else "does_not"] += 1
            done = start + len(batch)
            job.progress(done, len(todo), f"checked {done}/{len(todo)}")
    finally:
        if judge_pid:
            gpu.stop([judge_pid])
        evaluate._restore(before, reg)
    return counts
