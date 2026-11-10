"""The judge reads each reply the adapter would train on and says whether it answers the line before it, and
whether it's about one person's own life.

The export lays every Ohm turn out after the message its reply arrow points at (export.py), but in a busy
group chat about a third of real replies still don't answer that message: the arrow was guessed, or the
person was carrying on their own thread. An adapter trained on those learns to ignore what was just said.
Verdicts are kept in the project (reply_checks), so a reply is only ever checked once, and the export
leaves out the ones that don't answer (Documentation/Curator/training.md, "Checking replies with the judge").

Personal replies ("cant play, at work") would have Ohm claim a life that isn't his, so the export drops them too.
Older verdicts without `personal` are asked again, unless they already don't answer.

The judge is the brain's `judge` role, 30 replies to a call. Ohm's llama-server only stops for it when that's
the local Qwen model (brain.needs_gpu_swap).
"""

import brain

from .. import export, gpu
from ..errors import ApiError
from ..project import now_ms
from . import evaluate

INSTRUCTIONS = (f"{evaluate.SEPARATE} Each has one candidate, A. For each item, \"answers\" is true when A "
                "answers the last message of its chat. \"personal\" is true when A only makes sense coming from "
                "one particular real person: their own schedule or availability (\"im free at 5\", \"cant play "
                "tonight\"), where they are, their work, school or classes, family, or other events in their own "
                "life, or saying who they are in the group. Opinions, jokes, reactions, teasing and general talk "
                "are not personal.")
CONTEXT_CHARS = 800  # of chat before each reply; agreed with 3000 on 59/60 and is 30% faster
ITEM = {"type": "object", "properties": {"answers": {"type": "boolean"}, "personal": {"type": "boolean"}},
        "required": ["answers", "personal"]}


def pending(project) -> list[dict]:
    """Trained turns the judge hasn't seen yet: {key, context, reply}, each once."""
    data = export.build(project)
    with project.lock:
        # Both answered, or already out for not answering
        done = {r[0] for r in project.conn.execute(
            "SELECT reply_ids FROM reply_checks WHERE personal IS NOT NULL OR answers = 0")}
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
    return evaluate.judge_prompt(evaluate.transcript(it["context"], CONTEXT_CHARS), [("A", it["reply"])])


def run(project, reg, todo: list[dict], job) -> dict:
    """Check every reply in `todo`, saving each round's verdicts as they come."""
    counts = {"answers": 0, "does_not": 0, "personal": 0, "unreadable": 0}
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
                    if not isinstance((v or {}).get("answers"), bool) or not isinstance(v.get("personal"), bool):
                        counts["unreadable"] += 1  # not stored, so the next check asks again
                        continue
                    conn.execute("INSERT OR REPLACE INTO reply_checks (reply_ids, answers, personal, judge, checked_at) "
                                 "VALUES (?, ?, ?, ?, ?)",
                                 (it["key"], int(v["answers"]), int(v["personal"]), judge, now_ms()))
                    counts["answers" if v["answers"] else "does_not"] += 1
                    counts["personal"] += int(v["personal"])
            done = start + len(batch)
            job.progress(done, len(todo), f"checked {done}/{len(todo)}")
    finally:
        if judge_pid:
            gpu.stop([judge_pid])
        evaluate._restore(before, reg)
    return counts
