"""Demo data for tests and UI work: the Nov 5 "baro" conversation from the design.

This writes rows directly. It is NOT the importer (Smarth owns that).
Make a demo project:  python -m curator_service.tests.seed ~/Desktop/demo.ohm
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from curator_service.app import conversations, threads
from curator_service.app.project import Project

AUTHORS = [
    ("100", "KaptainDoosh", 4), ("101", "ELEVIST", 0), ("102", "unanticipatedinflation", 1),
    ("103", "420yolocoon", 2), ("104", "Zhingstin Ji", 3), ("105", "SerfShoop", 5), ("106", "Starcubed", 6),
]


def ts(h: int, m: int, day: int = 5) -> int:
    return int(datetime(2026, 11, day, h, m, tzinfo=timezone.utc).timestamp() * 1000)


# (id, author, time, text, mentions, reply_to)
MESSAGES = [
    ("1000", "100", ts(18, 12), "We running it this weekend?", [], None),
    ("1001", "101", ts(18, 38), "depends on other coons im down", [], "1000"),
    ("1002", "101", ts(18, 38) + 4000, "i will join if we can get more than 3", [], None),
    ("1003", "102", ts(18, 58), "we running barotrauma this night? @KaptainDoosh", ["100"], None),
    ("1004", "103", ts(19, 18), "I can do Sunday night", [], "1000"),
    ("1005", "104", ts(20, 31), "Baro could hit", [], None),
    ("1006", "100", ts(21, 50), "Mb I was out all day and just got home", [], "1003"),
    ("1007", "100", ts(21, 50) + 3000, "I’m down to baro", [], None),
    ("1008", "100", ts(21, 50) + 9000, "I’ll be ready in like 20 mins tho, I gotta shower", [], None),
    ("1009", "102", ts(21, 56), "bet", [], None),
    ("1010", "100", ts(22, 29), "@ELEVIST @420yolocoon @SerfShoop @Zhingstin Ji @Starcubed baro with me and arnav?",
     ["101", "103", "105", "104", "106"], None),
    # next conversation, the following evening
    ("1011", "104", ts(18, 5, day=6), "im in, give me 5", [], "1010"),
    ("1012", "101", ts(18, 6, day=6), "same", [], None),
    ("1013", "100", ts(18, 12, day=6), "pulling up now", [], None),
]


def seed(project: Project, split_gap_ms: int = 90 * 60 * 1000) -> None:
    with project.tx() as conn:
        project.set_settings(conn, {"split_gap_ms": split_gap_ms})
        conn.execute(
            "INSERT INTO sources (kind, file_name, sha256, guild_name, channel_id, channel_name, imported_at, message_count) "
            "VALUES ('demo', 'demo.json', 'demo', 'Demo server', 'c1', 'general', ?, ?)", (ts(23, 0), len(MESSAGES)))
        conn.executemany("INSERT INTO authors (id, name, color_index) VALUES (?, ?, ?)", AUTHORS)
        block_id, prev = None, None
        for mid, author, t, text, mentions, reply in MESSAGES:
            new_block = (prev is None or prev[1] != author or t - prev[2] > 5 * 60 * 1000 or reply is not None)
            if new_block:
                block_id = conn.execute(
                    "INSERT INTO blocks (channel_id, author_id, first_ts, last_ts) VALUES ('c1', ?, ?, ?)", (author, t, t)
                ).lastrowid
            else:
                conn.execute("UPDATE blocks SET last_ts = ? WHERE id = ?", (t, block_id))
            conn.execute(
                "INSERT INTO messages (id, channel_id, author_id, ts, type, content, content_text, reply_to_id, mentions, block_id) "
                "VALUES (?, 'c1', ?, ?, ?, ?, ?, ?, ?, ?)",
                (mid, author, t, "Reply" if reply else "Default", text, text, reply, json.dumps(mentions), block_id),
            )
            if reply:
                threads._write(conn, "block", block_id, reply, "accepted", "discord")
            prev = (mid, author, t)
        settings = project.settings()
        conversations.rebuild(conn, "c1", settings)
        threads.suggest_all(conn, settings)


if __name__ == "__main__":
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "demo.ohm").expanduser()
    path.unlink(missing_ok=True)
    p = Project.create(path, "Demo")
    seed(p)
    p.save()
    p.close()
    print(f"Wrote {path}")
