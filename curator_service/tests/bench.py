"""Time the Curator's hot paths on a big synthetic project (100k messages by default).

Writes rows the way an importer would (bulk inserts in one transaction), then times
conversations.rebuild, threads.suggest_all and the reads the UI makes on open.

    python -m curator_service.tests.bench            # 100k messages
    python -m curator_service.tests.bench 20000      # smaller, quicker
"""

import json
import os
import random
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault("CURATOR_DATA_DIR", tempfile.mkdtemp(prefix="curator-bench-"))

from curator_service.app import conversations, export, feed, memories, model_links, search, threads  # noqa: E402
from curator_service.app.project import Project  # noqa: E402

WORDS = ("baro barotrauma sub reactor sunday tonight weekend down bet game queue ranked anyone ready "
         "shower dinner work school exam lol ok same pulling up now wait who what when where why how "
         "minecraft server mod pack download patch update broken fixed crash lag ping wifi mic muted "
         "discord call join leave later tomorrow morning night sleep tired hungry pizza tacos movie").split()


@contextmanager
def timed(label: str, results: dict):
    t = time.perf_counter()
    yield
    results[label] = time.perf_counter() - t
    print(f"  {label:<34} {results[label]:8.2f}s", flush=True)


def generate(n: int, seed: int = 7):
    """Bursty chat: sessions of back-and-forth separated by hours of silence."""
    rng = random.Random(seed)
    authors = [(str(900 + i), f"user{i}", i % 8) for i in range(30)]
    weights = [1 / (i + 1) for i in range(len(authors))]
    msgs, t, mid = [], 1_600_000_000_000, 10_000_000
    while len(msgs) < n:
        t += rng.randint(35, 600) * 60_000  # silence between sessions
        people = rng.choices(authors, weights, k=rng.randint(2, 6))
        for _ in range(rng.randint(5, 120)):
            if len(msgs) >= n:
                break
            a = rng.choice(people)
            t += rng.choice((2_000, 5_000, 20_000, 60_000, 240_000, 400_000))
            mid += rng.randint(1, 50)
            text = " ".join(rng.choices(WORDS, k=rng.randint(1, 14)))
            if rng.random() < 0.15:
                text += "?"
            mentions = [rng.choice(people)[0]] if rng.random() < 0.08 else []
            reply = msgs[-rng.randint(1, min(6, len(msgs)))][0] if msgs and rng.random() < 0.1 else None
            msgs.append((str(mid), a[0], t, text, mentions, reply))
    return authors, msgs


def load(project: Project, authors, msgs, block_gap_ms: int) -> None:
    """Roughly what an importer does: one transaction, executemany, blocks computed in Python."""
    with project.tx() as conn:
        conn.execute(
            "INSERT INTO sources (kind, file_name, sha256, channel_id, channel_name, imported_at, message_count) "
            "VALUES ('bench', 'bench.json', 'bench', 'c1', 'general', 0, ?)", (len(msgs),))
        conn.executemany("INSERT INTO authors (id, name, color_index) VALUES (?, ?, ?)", authors)
        blocks, rows, links = [], [], []
        prev = None
        for mid, author, t, text, mentions, reply in msgs:
            if prev is None or prev[1] != author or t - prev[2] > block_gap_ms or reply is not None:
                blocks.append([len(blocks) + 1, author, t, t])
                if reply:
                    links.append(("block", str(len(blocks)), reply, "accepted", "discord", None, 0))
            else:
                blocks[-1][3] = t
            rows.append((mid, author, t, "Reply" if reply else "Default", text, text, reply, json.dumps(mentions), len(blocks)))
            prev = (mid, author, t)
        conn.executemany("INSERT INTO blocks (id, channel_id, author_id, first_ts, last_ts) VALUES (?, 'c1', ?, ?, ?)", blocks)
        conn.executemany(
            "INSERT INTO messages (id, channel_id, author_id, ts, type, content, content_text, reply_to_id, mentions, block_id) "
            "VALUES (?, 'c1', ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.executemany(
            "INSERT INTO links (unit_kind, unit_id, parent_id, status, origin, score, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            links)


def main(n: int) -> dict:
    results: dict = {}
    path = Path(tempfile.mkdtemp(prefix="curator-bench-")) / "bench.ohm"
    authors, msgs = generate(n)
    project = Project.create(path, "Bench")
    settings = project.settings()
    print(f"{n} messages")
    with timed("insert rows", results):
        load(project, authors, msgs, settings["block_gap_ms"])
    with timed("conversations.rebuild", results):
        with project.tx() as conn:
            conversations.rebuild(conn, "c1", settings)
    with timed("threads.suggest_all", results):
        with project.tx() as conn:
            counts = threads.suggest_all(conn, settings, "c1")
    print(f"    {counts}, {project.scalar('SELECT COUNT(*) FROM conversations')} conversations, "
          f"{project.scalar('SELECT COUNT(*) FROM blocks')} blocks")
    conn = project.conn
    with timed("conversations.listing", results):
        convs = conversations.listing(conn)
    with timed("feed.page (first)", results):
        feed.page(conn, "c1")
    with timed("feed.page (around last message)", results):
        feed.page(conn, "c1", around=msgs[-1][0])
    with timed("memories.people", results):
        memories.people(conn)
    with timed("search text", results):
        search.run(project, "baro tonight")
    with timed("search from: + is:unattached", results):
        search.run(project, "from:user1 is:unattached")
    with timed("search in:approved", results):
        search.run(project, "in:approved queue")
    with timed("model_links pending", results):
        model_links._pending(project, None)
    with project.tx() as c:  # approve half of the conversations so export has work
        c.execute("UPDATE conversations SET approved_at = 1 WHERE id % 2 = 0")
    with timed("export.preview (half approved)", results):
        export.preview(project)
    with timed("approve one big conversation", results):
        big = max(convs, key=lambda s: s["messages"])
        with project.tx() as c:
            conversations.approve(c, big["id"], confirm=True)
    with timed("style traits", results):
        with project.tx() as c:
            memories.refresh_styles(c)
    with timed("save", results):
        project.save()
    project.close()
    return results


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100_000)
