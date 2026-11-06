import json

import pytest

from curator_service.app import conversations, export, feed, search, threads
from curator_service.app.errors import ApiError
from curator_service.app.project import Project


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
def test_export_context_chain_and_cap(project):
    with project.tx() as conn:
        threads.set_parent(conn, "block", block_of(project, "1005"), "1003")
        threads.set_parent(conn, "message", "1007", "1005")
        conversations.approve(conn, 1, confirm=True)
    data = export.build(project)
    ex = next(e for e in data["examples"] if e["id"] == "msg:1007") if any(
        e["id"] == "msg:1007" for e in data["examples"]) else None
    pre = {e["name"]: e["share"] for e in data["by_author"]}
    assert pre["KaptainDoosh"] > 0.3
    for s in data["after_cap"]:
        assert s["share"] <= max(0.3, 1 / len(data["after_cap"])) + 0.15
    if ex:
        assert [c["speaker"] for c in ex["context"]][-2:] == ["unanticipatedinflation", "Zhingstin Ji"]
    # context never reaches into an unapproved conversation, and nothing from conversation 2 is a target
    assert all(e["conversation_id"] == 1 for e in data["examples"])
    msgs = export.chat_messages(data["examples"][0])
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]


def test_cap_is_feasible_with_two_people():
    examples = [{"id": str(i), "speaker_id": "a" if i < 70 else "b", "speaker": "x", "context": [], "target": str(i)}
                for i in range(100)]
    out, removed = export._cap(examples, 0.3, "seed")
    counts = {a: sum(e["speaker_id"] == a for e in out) for a in "ab"}
    assert counts == {"a": 30, "b": 30} and removed == 40
