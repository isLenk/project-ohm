"""Approves the conversations that show how the group talks: bulk-approve criteria first, then the brain's
`curate` role reads each one. The rest stay unapproved."""

import brain

from . import conversations, llm

SYSTEM = ("You choose training conversations for a chatbot that will talk like one Discord friend group, as a single "
          "personality merged from all of them. You judge how people talk, not whether it's polite.")
INSTRUCTIONS = (
    "Each item is a conversation from the group's chat, one message per line. Keep it if it shows how these people "
    "really talk to each other: humor, banter, roasts, opinions, running jokes, slang, reactions, how they answer "
    "each other. Leave it out if it's mostly logistics (\"on?\", \"gtg\", scheduling), bot commands, pasted links or "
    "game stats, one person talking to nobody, or too thin to show anyone's personality. Swearing and edgy jokes are "
    'fine and often the point. Answer {"keep": true or false, "why": "a few words"} for each item.')
ITEM = {"type": "object", "properties": {"keep": {"type": "boolean"}, "why": {"type": "string"}}, "required": ["keep"]}
MAX_CHARS = 2500  # the start is enough to tell
LINE_CHARS = 200
PACK = 6          # conversations per call
ROUND = 36        # per progress step and approval commit


def render(snap: dict, names: dict) -> str:
    lines = []
    for m in snap["messages"]:
        if m["excluded"]:
            continue
        text = " ".join((m["content_text"] or "").split())[:LINE_CHARS]
        if text:
            lines.append(f"{names.get(m['author_id'], 'someone')}: {text}")
    out = "\n".join(lines)
    return out if len(out) <= MAX_CHARS else out[:MAX_CHARS].rsplit("\n", 1)[0] + "\n(...)"


def run(project, job, criteria: dict | None = None, approve: bool = True) -> dict:
    """approve=False only returns the kept ids"""
    with project.lock:
        conn = project.conn
        found = conversations.candidates(conn, **(criteria or {}))
        cands = found["passed"]
        snaps = conversations._snapshot(conn, conversations._by_ids(conn, [c["id"] for c in cands]))
        names = {r["id"]: r["display_name"] or r["name"] for r in conn.execute("SELECT id, name, display_name FROM authors")}
    items = [(c["id"], render(snaps[c["id"]], names)) for c in cands]
    kept, left, unanswered, reasons = [], [], 0, {}
    for start in range(0, len(items), ROUND):
        batch = items[start:start + ROUND]
        job.progress(start, len(items), f"reading conversations {start + 1}-{start + len(batch)} of {len(items)}")
        verdicts = brain.run_packed([text for _, text in batch], SYSTEM, INSTRUCTIONS, ITEM, size=PACK, role="curate",
                                    workers=3, temperature=0)
        keep_now = []
        for (cid, _), v in zip(batch, verdicts):
            if v is None:
                unanswered += 1
                continue
            (keep_now if v.get("keep") else left).append(cid)
            reasons[cid] = (v.get("why") or "")[:80]
        if keep_now and approve:  # as it goes, so a stopped job keeps its work
            with project.tx() as conn:
                conversations.approve_many(conn, keep_now)
        kept += keep_now
    job.progress(len(items), len(items), "done")
    return {"read": len(items), "approved": len(kept) if approve else 0, "kept": kept, "left_out": len(left),
            "unanswered": unanswered,
            "skipped_by_criteria": found["skipped"], "backend": llm.brain_backend("curate"),
            "left_out_why": {cid: reasons.get(cid, "") for cid in left[:50]}}
