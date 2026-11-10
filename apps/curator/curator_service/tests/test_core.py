import json
import sqlite3

import pytest

from curator_service.app import conversations, export, feed, schema, search, threads
from curator_service.app.errors import ApiError
from curator_service.app.project import Project
from curator_service.tests.seed import seed


def block_of(p, message_id):
    return str(p.scalar("SELECT block_id FROM messages WHERE id = ?", (message_id,)))


# ── project file ────────────────────────────────────────────────────────────
def test_save_and_reopen_round_trip(tmp_path, project):
    project.save()
    assert not project.dirty
    path = project.path
    project.close()
    again = Project.open(path)
    assert again.scalar("SELECT COUNT(*) FROM messages") == 14
    assert not again.dirty
    again.close()


def test_recovery_offered_for_unsaved_work(tmp_path, project):
    project.save()
    with project.tx() as conn:
        conn.execute("UPDATE messages SET excluded = 1 WHERE id = '1009'")
    path = project.path
    project.conn.close()  # simulate a crash: working copy left behind
    with pytest.raises(ApiError) as e:
        Project.open(path)
    assert e.value.code == "recovery_available"
    recovered = Project.open(path, recover=True)
    assert recovered.scalar("SELECT excluded FROM messages WHERE id = '1009'") == 1
    assert recovered.dirty
    recovered.close()


def test_rejects_non_project(tmp_path):
    import sqlite3

    other = tmp_path / "x.ohm"
    sqlite3.connect(other).execute("CREATE TABLE t (a)")
    with pytest.raises(ApiError) as e:
        Project.open(other)
    assert e.value.code == "not_a_project"


# ── conversations from the seed ─────────────────────────────────────────────
def test_seed_has_two_conversations(project):
    convs = project.all("SELECT * FROM conversations ORDER BY first_ts")
    assert [c["first_message_id"] for c in convs] == ["1000", "1011"]
    assert convs[0]["last_message_id"] == "1010"


# ── links ───────────────────────────────────────────────────────────────────
def test_block_link_and_split_line(project):
    kaptain = block_of(project, "1006")
    with project.tx() as conn:
        # "I'm down to baro" answers Zhingstin, the rest of the block stays on unanticipatedinflation
        r = threads.set_parent(conn, "message", "1007", "1005")
    assert r["inverse"]["body"]["link"] is None
    with project.lock:
        path = threads.thread_path(project.conn, "1007")
        units = conversations.units_in(project.conn, conversations.get(project.conn, 1))
    assert [u["message_ids"] for u in path][-1] == ["1007"]
    block_unit = next(u for u in units if u["kind"] == "block" and u["id"] == kaptain)
    assert [m["id"] for m in block_unit["messages"]] == ["1006", "1008"]


@pytest.mark.parametrize("kind,unit,parent,code", [
    ("block", "msg:1000", "1004", "parent_not_earlier"),
    ("message", "1007", "1006", "parent_in_unit"),
    ("message", "1007", "9999", "parent_missing"),
])
def test_validation(project, kind, unit, parent, code):
    unit_id = block_of(project, "1000") if unit == "msg:1000" else unit
    if code == "parent_in_unit":
        kind, unit_id = "block", block_of(project, "1006")
    with pytest.raises(ApiError) as e:
        with project.tx() as conn:
            threads.set_parent(conn, kind, unit_id, parent)
    assert e.value.code == code


def test_detach_then_undo(project):
    b = block_of(project, "1001")
    with project.tx() as conn:
        r = threads.delete(conn, "block", b)
    assert threads.link_row(project.conn, "block", b) is None
    with project.tx() as conn:
        threads.restore(conn, **{"kind": "block", "unit_id": b, "link": r["inverse"]["body"]["link"]})
    link = threads.link_row(project.conn, "block", b)
    assert link["parent_id"] == "1000" and link["origin"] == "discord"


def test_edit_unapproves_conversation(project):
    with project.tx() as conn:
        conversations.approve(conn, 1, confirm=True)
    with project.tx() as conn:
        r = threads.set_parent(conn, "block", block_of(project, "1009"), "1008")
    assert r["conversations_changed"] == [{"id": 1, "approved": False}]


def test_reject_records_and_does_not_resuggest(project):
    b = block_of(project, "1005")
    with project.tx() as conn:
        threads._write(conn, "block", b, "1003", "suggested", "suggested", 0.7)
        threads.reject(conn, "block", b, project.settings())
    assert project.one("SELECT * FROM rejected_suggestions WHERE unit_id = ?", (b,))["parent_id"] == "1003"
    link = threads.link_row(project.conn, "block", b)
    assert link is None or link["parent_id"] != "1003"


def test_suggester_marks_mention_reply(project):
    # "bet" right after Kaptain's block: recency + turn taking, but no mention; the mass ping is unattached
    with project.lock:
        mass = threads.link_row(project.conn, "block", block_of(project, "1010"))
    assert mass is None or mass["status"] != "accepted"


# ── approval ────────────────────────────────────────────────────────────────
def test_approve_needs_confirm_when_undecided(project):
    with pytest.raises(ApiError) as e:
        with project.tx() as conn:
            conversations.approve(conn, 1, confirm=False)
    assert e.value.code == "needs_confirm"
    assert e.value.extra["unattached"] + e.value.extra["suggested"] > 0
    with project.tx() as conn:
        s = conversations.approve(conn, 1, confirm=True)
    assert s["approved"] and s["suggested"] == 0
    # it reports what it accepted, so undo can put the suggestions back
    assert s["accepted"] and all(a["link"]["status"] == "suggested" for a in s["accepted"])
    with project.tx() as conn:
        for a in s["accepted"]:
            threads.restore(conn, a["unit_kind"], a["unit_id"], a["link"])
    for a in s["accepted"]:
        assert threads.link_row(project.conn, a["unit_kind"], a["unit_id"])["status"] == "suggested"


# ── split / merge ───────────────────────────────────────────────────────────
def test_split_inside_block_cuts_block_and_copies_link(project):
    before = block_of(project, "1006")
    with project.tx() as conn:
        a, b = conversations.split(conn, "1007")
    assert b["first_message_id"] == "1007"
    after = block_of(project, "1007")
    assert after != before
    assert threads.link_row(project.conn, "block", after)["parent_id"] == "1003"
    with project.tx() as conn:
        merged = conversations.merge_up(conn, b["id"])
    assert merged["last_message_id"] == "1010"


def test_rebuild_keeps_manual_splits(project):
    with project.tx() as conn:
        conversations.split(conn, "1004")
        conversations.split(conn, "1009")
        conversations.rebuild(conn, "c1", project.settings())
    firsts = [c["first_message_id"] for c in project.all("SELECT * FROM conversations ORDER BY first_ts")]
    assert firsts == ["1000", "1004", "1009", "1011"]


# ── feed ────────────────────────────────────────────────────────────────────
def test_feed_pages_and_markers(project):
    with project.lock:
        page = feed.page(project.conn, limit=3)
        assert page["items"][0]["type"] == "conversation_start"
        assert page["next_cursor"] and page["prev_cursor"] is None
        nxt = feed.page(project.conn, cursor=page["next_cursor"], limit=100)
        assert any(i["type"] == "conversation_start" for i in nxt["items"])
        around = feed.page(project.conn, around="1007", limit=4)
        ids = [m["id"] for i in around["items"] if i["type"] == "block" for m in i["messages"]]
        assert "1007" in ids
        # links to parents outside the page carry a preview for the edge stub
        far = feed.page(project.conn, around="1004", limit=1)
        blk = next(i for i in far["items"] if i["type"] == "block")
        assert blk["link"]["parent_preview"] == "We running it this weekend?"


# ── search ──────────────────────────────────────────────────────────────────
def test_search_text_person_and_regex(project):
    r = search.run(project, "from:Kaptain baro")
    assert {m["id"] for m in r["messages"]} == {"1007", "1010"}
    assert r["messages"][0]["highlights"]
    r = search.run(project, r"/\bbaro\b/")
    assert "1005" in {m["id"] for m in r["messages"]}
    r = search.run(project, "barotrauma", mode="text")
    assert [m["id"] for m in r["messages"]] == ["1003"]
    with pytest.raises(ApiError):
        search.run(project, "/(unclosed/")


def test_search_operators(project):
    r = search.run(project, "is:unattached")
    assert all(m["id"] for m in r["messages"])
    assert search.parse('"exact phrase" @Zhing is:similar before:2026-11-06')["phrases"] == ["exact phrase"]


# ── export ──────────────────────────────────────────────────────────────────
def test_export_replays_each_person_as_ohm(project, monkeypatch):
    monkeypatch.setattr(export, "MAX_REPLY_LINES", 4)  # KaptainDoosh's "Mb I was out" is 3 lines
    with project.tx() as conn:
        conversations.approve(conn, 1, confirm=True)
    data = export.build(project)
    system = export.system_prompt()
    assert data["examples"] and all(e["conversation_id"] == 1 for e in data["examples"])
    for e in data["examples"]:
        roles = [m["role"] for m in e["messages"]]
        assert roles[0] == "system" and e["messages"][0]["content"] == system
        assert roles[1:] == ["user", "assistant"] * (len(roles[1:]) // 2)
        assert e["assistant_turns"] == roles.count("assistant")
    kaptain = next(e for e in data["examples"] if e["speaker"] == "KaptainDoosh")
    context, reply = kaptain["messages"][1]["content"], kaptain["messages"][2]["content"]
    assert "unanticipatedinflation: we running barotrauma this night? @Ohm" in context
    assert "KaptainDoosh" not in context
    assert reply.startswith("Mb I was out all day")  # back-to-back lines are one turn
    # in someone else's replay KaptainDoosh keeps their name
    other = next(e for e in data["examples"] if e["speaker"] == "420yolocoon")
    assert "@KaptainDoosh" in other["messages"][1]["content"]
    assert data["by_author"] and sum(s["count"] for s in data["after_cap"]) == sum(
        e["assistant_turns"] for e in data["examples"])


def test_export_trains_only_replies_to_the_last_line(project, monkeypatch):
    monkeypatch.setattr(export, "MAX_REPLY_LINES", 4)  # KaptainDoosh's "Mb I was out" is 3 lines
    with project.tx() as conn:
        conversations.approve(conn, 1, confirm=True)
        # KaptainDoosh's roll call answers ELEVIST from before KaptainDoosh's own "Mb I was out" turn
        threads._write(conn, "block", block_of(project, "1010"), "1001", "accepted", "manual")
    data = export.build(project)
    # The opener and Zhingstin's "Baro could hit" start topics (root arrows); the bot only ever answers
    assert not any(e["speaker"] == "Zhingstin Ji" for e in data["examples"])
    assert data["dropped"]["starts a topic"] == 2
    kaptain = next(e for e in data["examples"] if e["speaker"] == "KaptainDoosh")
    # "Mb I was out" answers 1003, two lines up: that line is moved to the end so it's what Ohm answers
    context, reply = kaptain["messages"][-2]["content"], kaptain["messages"][-1]["content"]
    assert context.splitlines()[-1] == "unanticipatedinflation: we running barotrauma this night? @Ohm"
    assert "Baro could hit" in context.splitlines()[-2]
    assert reply.startswith("Mb I was out all day")
    assert "1010" not in kaptain["source_message_ids"] and data["dropped"]["answers something older"] == 1
    with project.lock:
        estimate = conversations._replies(conversations._snapshot(project.conn, [conversations.get(project.conn, 1)])[1],
                                          set())
    assert sum(estimate.values()) == sum(s["count"] for s in data["by_author"])


def test_export_carries_parent_into_a_later_conversation(project):
    # Conversation 2 opens with Zhingstin answering KaptainDoosh's 1010 from the night before
    with project.tx() as conn:
        threads._write(conn, "block", block_of(project, "1013"), "1011", "accepted", "manual")
        conversations.approve(conn, 2, confirm=True)
    data = export.build(project)
    zhing = next(e for e in data["examples"] if e["speaker"] == "Zhingstin Ji")
    context, reply = zhing["messages"][1]["content"], zhing["messages"][2]["content"]
    assert context.startswith("KaptainDoosh: @ELEVIST")
    assert "@Ohm" in context  # Zhingstin's name becomes Ohm in their own replay
    assert reply == "im in, give me 5"
    assert "1010" not in zhing["source_message_ids"]  # context, never a target
    # KaptainDoosh isn't replayed against their own carried-over line
    kaptain = next(e for e in data["examples"] if e["speaker"] == "KaptainDoosh")
    assert "@ELEVIST" not in kaptain["messages"][1]["content"]
    assert kaptain["messages"][-1]["content"] == "pulling up now"


def test_export_starts_over_after_a_long_silence(project, monkeypatch):
    monkeypatch.setattr(export, "MAX_REPLY_LINES", 4)  # KaptainDoosh's "Mb I was out" is 3 lines
    with project.tx() as conn:
        conversations.approve(conn, 1, confirm=True)
        conn.execute("UPDATE meta SET value = ? WHERE key = 'split_gap_ms'", (json.dumps(30 * 60_000),))
    kaptain = [e for e in export.build(project)["examples"] if e["speaker"] == "KaptainDoosh"]
    late = next(e for e in kaptain if e["messages"][-1]["content"].startswith("Mb I was out"))
    # 79 quiet minutes before KaptainDoosh's reply: only the message it answers comes along
    assert late["segment"] > 0
    assert late["messages"][1]["content"] == "unanticipatedinflation: we running barotrauma this night? @Ohm"
    assert all("Baro could hit" not in m["content"] for m in late["messages"])


def test_export_drops_replies_over_two_lines(project):
    with project.tx() as conn:
        conversations.approve(conn, 1, confirm=True)
    data = export.build(project)
    # KaptainDoosh's "Mb I was out" is 3 lines
    assert not any(m["content"].startswith("Mb I was out") for e in data["examples"] for m in e["messages"]
                   if m["role"] == "assistant")
    assert data["dropped"]["too many lines"] == 1
    assert all(m["content"].count("\n") < export.MAX_REPLY_LINES for e in data["examples"] for m in e["messages"]
               if m["role"] == "assistant")


def test_export_drops_links_pings_and_transcripts(project):
    with project.tx() as conn:
        threads._write(conn, "block", block_of(project, "1013"), "1011", "accepted", "manual")
        # Zhingstin answers KaptainDoosh's 1010 and pings them first; the bot's reply would ping them anyway
        conn.execute("UPDATE messages SET content_text = '@KaptainDoosh im in, give me 5' WHERE id = '1011'")
        # KaptainDoosh's 1013 becomes a link: the model can't send real ones
        conn.execute("UPDATE messages SET content_text = 'https://tenor.com/view/x' WHERE id = '1013'")
        conversations.approve(conn, 2, confirm=True)
    data = export.build(project)
    zhing = next(e for e in data["examples"] if e["speaker"] == "Zhingstin Ji")
    assert zhing["messages"][-1]["content"] == "im in, give me 5"
    assert not any(e["speaker"] == "KaptainDoosh" and "1013" in e["source_message_ids"] for e in data["examples"])
    assert data["dropped"]["links or media"] == 1


def test_export_reply_filters():
    unping = export._unping(["ELEVIST", "elevist"])
    assert unping("@ELEVIST @elevist, yo @ELEVIST") == "yo @ELEVIST"
    assert unping("@ELEVISTS yo") == "@ELEVISTS yo"  # someone else
    msgs = [{"ts": t} for t in (0, 60_000, 60_000 + 31 * 60_000, 60_000 + 32 * 60_000)]
    assert [len(s) for s in export._segments(msgs, 30 * 60_000)] == [2, 2]
    assert export._media({"content_text": "look https://x.y", "attachments": "[]"})
    assert export._media({"content_text": "crazy snipe", "attachments": '[{"fileName": "a.png"}]'})
    assert not export._media({"content_text": "crazy snipe", "attachments": "[]"})


def test_export_chunks_long_conversations():
    turns = []
    for i in range(10):
        turns += [{"role": "user", "content": "u" * 100, "ids": []}, {"role": "assistant", "content": "a" * 100, "ids": [str(i)]}]
    chunks = export._chunk(turns, 600)
    assert len(chunks) > 1
    for ch in chunks:
        assert ch[0]["role"] == "user" and ch[-1]["role"] == "assistant"
    # every reply is trained at least once, and chunks overlap by OVERLAP_PAIRS pairs
    assert {i for ch in chunks for t in ch for i in t["ids"]} == {str(i) for i in range(10)}
    assert chunks[1][:2 * export.OVERLAP_PAIRS] == chunks[0][-2 * export.OVERLAP_PAIRS:]


def test_cap_limits_assistant_turns():
    examples = [{"id": str(i), "speaker_id": "a" if i < 70 else "b", "assistant_turns": 1} for i in range(100)]
    out, removed = export._cap(examples, 0.3, "seed")
    counts = {a: sum(e["speaker_id"] == a for e in out) for a in "ab"}
    assert counts == {"a": 30, "b": 30} and removed == 40


def test_textless_messages_removed_on_open(tmp_path):
    path = tmp_path / "pics.ohm"
    p = Project.create(path, "Pics")
    seed(p)
    with p.tx() as conn:
        # 1003 becomes an image-only message that 1006 replied to; it continued from 1000.
        conn.execute("UPDATE messages SET content = '', content_text = ' ', attachments = '[{\"name\": \"y.png\"}]' "
                     "WHERE id IN ('1003', '1013')")
        threads._write(conn, "block", block_of(p, "1003"), "1000", "accepted", "manual")
    gone_block = block_of(p, "1003")
    p.save()
    p.close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 4")
    raw.commit()
    raw.close()

    p = Project.open(path)
    try:
        assert p.scalar("PRAGMA user_version") == schema.PROJECT_VERSION
        assert p.scalar("SELECT COUNT(*) FROM messages WHERE id IN ('1003', '1013')") == 0
        assert p.scalar("SELECT COUNT(*) FROM blocks WHERE id = ?", (gone_block,)) == 0
        # the reply to the picture now continues the picture's thread
        assert threads.link_row(p.conn, "block", str(block_of(p, "1006")))["parent_id"] == "1000"
        assert p.scalar("SELECT reply_to_id FROM messages WHERE id = '1006'") is None
        assert p.scalar("SELECT COUNT(*) FROM links WHERE parent_id IN ('1003', '1013')") == 0
        # the second conversation ended on the picture; it now ends on the last message with text
        assert p.scalar("SELECT last_message_id FROM conversations ORDER BY first_ts DESC LIMIT 1") == "1012"
        assert p.scalar("PRAGMA foreign_key_check") is None
    finally:
        p.close()


def test_old_project_migrates(tmp_path):
    path = tmp_path / "old.ohm"
    p = Project.create(path, "Old")
    seed(p)
    with p.tx() as conn:
        threads._write(conn, "block", block_of(p, "1009"), "1008", "suggested", "suggested", 0.7)
        threads.reject(conn, "block", block_of(p, "1009"), p.settings())
    p.save()
    p.close()

    # rewind the file to the v1 schema
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE links_v1 (
          id INTEGER PRIMARY KEY,
          unit_kind TEXT NOT NULL CHECK (unit_kind IN ('block','message')),
          unit_id TEXT NOT NULL, parent_id TEXT REFERENCES messages(id),
          status TEXT NOT NULL CHECK (status IN ('accepted','suggested','root')),
          origin TEXT NOT NULL CHECK (origin IN ('discord','suggested','manual')),
          score REAL, updated_at INTEGER NOT NULL, UNIQUE (unit_kind, unit_id));
        INSERT INTO links_v1 SELECT * FROM links;
        DROP TABLE links;
        ALTER TABLE links_v1 RENAME TO links;
        CREATE INDEX links_parent ON links (parent_id);
        CREATE TABLE rejected_v1 (unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL, parent_id TEXT NOT NULL,
          PRIMARY KEY (unit_kind, unit_id, parent_id));
        INSERT INTO rejected_v1 SELECT unit_kind, unit_id, parent_id FROM rejected_suggestions;
        DROP TABLE rejected_suggestions;
        ALTER TABLE rejected_v1 RENAME TO rejected_suggestions;
        DROP TABLE model_link_scans;
        DROP INDEX messages_feed;
        CREATE INDEX messages_feed ON messages (channel_id, ts, id);
        DROP INDEX blocks_author;
        PRAGMA user_version = 1;
    """)
    links_before = raw.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    raw.close()

    p = Project.open(path)
    try:
        assert p.scalar("PRAGMA user_version") == schema.PROJECT_VERSION
        feed_index = p.scalar("SELECT sql FROM sqlite_master WHERE name = 'messages_feed'")
        assert "CAST(id AS INTEGER)" in feed_index
        assert p.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name = 'blocks_author'") == 1
        assert p.scalar("SELECT COUNT(*) FROM links") == links_before
        assert threads.suggester_stats(p.conn)["rejected"] == 1  # old rejections count as the heuristic's
        with p.tx() as conn:
            threads._write(conn, "block", block_of(p, "1012"), "1011", "suggested", "model", 0.4)
        assert p.scalar("SELECT COUNT(*) FROM model_link_scans") == 0
    finally:
        p.close()


# ── training ────────────────────────────────────────────────────────────────
def test_held_out_conversations_are_whole_and_stable():
    from curator_service.app.train import worker

    rows = [{"conversation_id": c} for c in range(40) for _ in range(3)]
    held = worker.held_out(rows, 1337)
    assert len(held) == 4 and held == worker.held_out(list(reversed(rows)), 1337)
    assert worker.held_out(rows[:30], 1337) == set()  # 10 conversations: all of them are needed


def test_curator_questions_go_to_the_base_model(monkeypatch):
    from curator_service.app import llm

    class R:
        ok = True

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

        def raise_for_status(self):
            pass

    sent = []
    monkeypatch.setattr(llm, "_adapters", (0.0, None))
    monkeypatch.setattr(llm.requests, "get", lambda url, **k: R([{"id": 0, "scale": 1.0}, {"id": 1, "scale": 1.0}]))
    monkeypatch.setattr(llm.requests, "post", lambda url, json, **k: sent.append(json) or R(
        {"choices": [{"message": {"content": "ok"}}]}))
    assert llm.chat("sys", "hi") == "ok"
    assert sent[0]["lora"] == [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 0.0}]
