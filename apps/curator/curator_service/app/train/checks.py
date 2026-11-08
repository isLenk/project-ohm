"""The judge model reads each reply the adapter would train on and says whether it answers the line before it.

The export lays every Ohm turn out after the message its reply arrow points at (export.py), but in a busy
group chat about a third of real replies still don't answer that message: the arrow was guessed, or the
person was carrying on their own thread. An adapter trained on those learns to ignore what was just said.
Verdicts are kept in the project (reply_checks), so a reply is only ever checked once, and the export
leaves out the ones that don't answer (Documentation/Curator/training.md, "Checking replies with the judge").
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import export, gpu
from ..config import JUDGE_MODEL
from ..errors import ApiError
from ..project import now_ms
from . import evaluate


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
    if not Path(JUDGE_MODEL).is_file():
        raise ApiError(409, "no_judge", f"The judge model isn't at {JUDGE_MODEL}. Set CURATOR_JUDGE_MODEL.")
    if reg.one("SELECT id FROM train_runs WHERE status IN ('queued','running')"):
        raise ApiError(409, "run_active", "Wait for the training run to finish: they'd share the GPU.")
    return pending(project)


def ask(it: dict) -> dict | None:
    """{"A": answers} for one reply, or None when the judge's answer doesn't fit."""
    prompt = evaluate.judge_prompt(evaluate.transcript(it["context"]), [("A", it["reply"])])
    return evaluate.parse_answers(evaluate._ask_judge(evaluate.JUDGE_SYSTEM, prompt), ["A"])


def run(project, reg, todo: list[dict], job, judge_reply=ask) -> dict:
    """Check every reply in `todo`. Ohm's llama-server stops while the judge runs and comes back after."""
    counts = {"answers": 0, "does_not": 0, "unreadable": 0}
    if not todo:
        return counts
    judge = Path(JUDGE_MODEL).stem
    before = gpu.running_llama()
    judge_pid = None

    def one(it):
        return it, None if job.cancelled else judge_reply(it)

    try:
        job.progress(0, len(todo), "loading the judge model")
        if before:
            gpu.stop([before["pid"]])
        judge_pid = evaluate._start_judge()
        with ThreadPoolExecutor(evaluate.JUDGE_SLOTS) as pool:
            for n, (it, verdict) in enumerate(pool.map(one, todo), 1):
                if job.cancelled:
                    break
                if verdict is None:
                    counts["unreadable"] += 1
                else:
                    with project.tx() as conn:
                        conn.execute("INSERT OR REPLACE INTO reply_checks (reply_ids, answers, judge, checked_at) "
                                     "VALUES (?, ?, ?, ?)", (it["key"], int(verdict["A"]), judge, now_ms()))
                    counts["answers" if verdict["A"] else "does_not"] += 1
                job.progress(n, len(todo), f"checked {n}/{len(todo)}")
    finally:
        if judge_pid:
            gpu.stop([judge_pid])
        evaluate._restore(before, reg)
    return counts
