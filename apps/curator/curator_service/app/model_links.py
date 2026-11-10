"""Reply arrows guessed by the local LLM, for blocks the heuristic suggester left unattached.

The heuristic in threads.py ranks earlier messages; the model reads the top few and picks
one (or none). Its picks are ordinary suggestions with origin 'model', so they go through
the same accept/reject flow, and approving a conversation accepts them.
Runs go newest first. See Documentation/Curator/threading.md.
"""

import re
from typing import Optional

import requests

from . import llm, threads
from .errors import ApiError, not_found
from .project import now_ms

CHOICES = 5

SYSTEM = (
    "You read a Discord group chat and work out which earlier message a new message is replying to.\n"
    "You get a numbered list of earlier messages, oldest first, then the new message.\n"
    'Reply with JSON only: {"reply_to": <the number of the message it answers or reacts to>}.\n'
    'If it starts a new topic, or none of them fit, reply exactly {"reply_to": null}.'
)


def _one_line(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def prompt(names: dict, choices: list[dict], author: str, text: str) -> str:
    lines = [f"[{i}] {names.get(c['author_id'], 'someone')}: {_one_line(c['content_text'], 300)}"
             for i, c in enumerate(choices, 1)]
    return "Earlier messages:\n" + "\n".join(lines) + f"\n\nNew message from {author}:\n{_one_line(text, 600)}"


def pick(answer, count: int) -> Optional[int]:
    """Index into the choices, or None for "no reply" and anything malformed."""
    if not isinstance(answer, dict):
        return None
    n = answer.get("reply_to")
    if isinstance(n, str) and n.strip().isdigit():
        n = int(n)
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= count:
        return None
    return n - 1


def _pending(project, conversation_id: Optional[int]) -> list[int]:
    """Unlinked blocks the model hasn't looked at, outside approved conversations."""
    sql = (
        "SELECT b.id FROM blocks b "
        "LEFT JOIN links l ON l.unit_kind = 'block' AND l.unit_id = CAST(b.id AS TEXT) "
        "LEFT JOIN model_link_scans s ON s.block_id = b.id "
        "WHERE l.id IS NULL AND s.block_id IS NULL "
        "AND EXISTS (SELECT 1 FROM messages m WHERE m.block_id = b.id AND m.excluded = 0) "
        "AND NOT EXISTS (SELECT 1 FROM conversations c WHERE c.id = " + threads.conversation_sql("b.channel_id", "b.first_ts") +
        "  AND c.last_ts >= b.first_ts AND c.approved_at IS NOT NULL)"
    )
    params: list = []
    if conversation_id is not None:
        conv = project.one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        if not conv:
            raise not_found("Conversation")
        sql += " AND b.channel_id = ? AND b.first_ts BETWEEN ? AND ?"
        params += [conv["channel_id"], conv["first_ts"], conv["last_ts"]]
    # Newest first: recent chat is what gets curated and trained on first.
    return [r["id"] for r in project.all(sql + " ORDER BY b.first_ts DESC, b.id DESC", params)]


def pending_count(project) -> int:
    return len(_pending(project, None))


def _mark(conn, block_id: int) -> None:
    conn.execute("INSERT OR REPLACE INTO model_link_scans (block_id, scanned_at) VALUES (?, ?)", (block_id, now_ms()))


def _apply(project, bid: int, scored, choices, answer) -> str:
    """Save the model's pick: "suggested", "none", or "skipped" if linked by hand meanwhile"""
    n = pick(answer, len(choices))
    with project.tx() as conn:
        _mark(conn, bid)
        if n is None:
            return "none"
        if threads.link_row(conn, "block", str(bid)):
            return "skipped"
        parent = choices[n]
        try:
            threads.validate(conn, "block", str(bid), parent["id"])
        except ApiError:
            return "none"
        score = next(s for s, c in scored if c["id"] == parent["id"])
        threads._write(conn, "block", bid, parent["id"], "suggested", "model", round(score, 3))
        return "suggested"


WORKERS = 4  # llama-server's slots


def reguess(project, job, conversation_ids: list[int]) -> dict:
    """Redo the model's unaccepted guesses in these conversations; accepted arrows stay"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    convs = []
    for i in range(0, len(conversation_ids), 500):
        part = conversation_ids[i:i + 500]
        convs += project.all(f"SELECT * FROM conversations WHERE id IN ({','.join('?' * len(part))})", part)
    blocks, cleared = [], 0
    with project.tx() as conn:
        for c in convs:
            blocks += [r[0] for r in conn.execute(
                "SELECT id FROM blocks WHERE channel_id = ? AND first_ts BETWEEN ? AND ?",
                (c["channel_id"], c["first_ts"], c["last_ts"]))]
        for i in range(0, len(blocks), 500):
            part = blocks[i:i + 500]
            marks = ",".join("?" * len(part))
            cleared += conn.execute(
                f"DELETE FROM links WHERE unit_kind = 'block' AND origin = 'model' AND status = 'suggested' "
                f"AND unit_id IN ({marks})", [str(b) for b in part]).rowcount
            conn.execute(f"DELETE FROM model_link_scans WHERE block_id IN ({marks})", part)
        todo = [b for b in blocks if not threads.link_row(conn, "block", str(b))]
    names = {a["id"]: a["display_name"] or a["name"] for a in project.all("SELECT id, name, display_name FROM authors")}

    def ask(bid):
        with project.lock:
            msgs = [m for m in threads.block_messages(project.conn, bid) if not m["excluded"]]
            scored = threads.score_candidates(project.conn, bid)[:CHOICES]
        if not msgs or not scored:
            return bid, scored, [], None
        choices = sorted((c for _, c in scored), key=threads.key)
        text = "\n".join(m["content_text"] for m in msgs)
        answer = llm.chat_json(SYSTEM, prompt(names, choices, names.get(msgs[0]["author_id"], "someone"), text),
                               max_tokens=60)
        return bid, scored, choices, answer

    counts = {"suggested": 0, "none": 0, "skipped": 0, "failed": 0}
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = [pool.submit(ask, b) for b in todo]
        for i, f in enumerate(as_completed(futures)):
            if job.cancelled:
                for other in futures:
                    other.cancel()
                break
            job.progress(i, len(todo), f"{counts['suggested']} guessed")
            try:
                bid, scored, choices, answer = f.result()
            except requests.RequestException:
                counts["failed"] += 1
                continue
            if not choices:
                with project.tx() as conn:
                    _mark(conn, bid)
                counts["none"] += 1
                continue
            counts[_apply(project, bid, scored, choices, answer)] += 1
    job.progress(len(todo), len(todo), f"{counts['suggested']} guessed")
    return {"conversations": len(convs), "blocks": len(blocks), "cleared": cleared, "asked": len(todo), **counts}


def guess(project, job, conversation_id: Optional[int] = None) -> dict:
    """Ask the model about every pending block (or one conversation's). Only the
    model's answers mark a block as looked at, so failed requests are retried next run."""
    if not llm.up():
        raise ApiError(503, "llm_down", "llama-server isn't running on :5000, so the model can't guess replies.")
    blocks = _pending(project, conversation_id)
    names = {a["id"]: a["display_name"] or a["name"]
             for a in project.all("SELECT id, name, display_name FROM authors")}
    suggested = no_reply = failed = 0
    changed: list[int] = []
    stopped = None
    for i, bid in enumerate(blocks):
        if job.cancelled:
            break
        job.progress(i, len(blocks), f"{suggested} guessed")
        with project.lock:
            msgs = [m for m in threads.block_messages(project.conn, bid) if not m["excluded"]]
            scored = threads.score_candidates(project.conn, bid)[:CHOICES]
        if not msgs or not scored:
            with project.tx() as conn:
                _mark(conn, bid)
            no_reply += 1
            continue
        choices = sorted((c for _, c in scored), key=threads.key)
        text = "\n".join(m["content_text"] for m in msgs)
        try:
            answer = llm.chat_json(SYSTEM, prompt(names, choices, names.get(msgs[0]["author_id"], "someone"), text),
                                   max_tokens=60)
        except requests.RequestException:
            if not llm.up():
                stopped = "llama-server stopped answering."
                break
            failed += 1
            continue
        result = _apply(project, bid, scored, choices, answer)
        if result == "suggested":
            suggested += 1
            changed.append(bid)
        elif result == "none":
            no_reply += 1
    job.progress(len(blocks), len(blocks), f"{suggested} guessed")

    from .feed import block_item  # feed imports threads

    # A project-wide run can change thousands of blocks; the UI reloads the feed instead.
    units = []
    if conversation_id is not None:
        with project.lock:
            units = [block_item(project.conn, b, page_ids=None) for b in changed]
    return {"blocks": len(blocks), "suggested": suggested, "no_reply": no_reply, "failed": failed,
            "stopped": stopped, "units": units}
