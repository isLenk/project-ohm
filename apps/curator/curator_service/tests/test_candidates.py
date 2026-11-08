"""Finding good conversations and approving them in bulk (conversations.candidates / approve_many)."""

import json

from fastapi.testclient import TestClient

from curator_service.app import conversations, export, main
from curator_service.app.project import Project, state
from curator_service.tests.seed import seed

HOURS_2 = 2 * 3600 * 1000
LOOSE = {"max_gap_ms": HOURS_2, "min_attached": 0}  # the seed's first conversation has a 79-minute silence


def test_defaults_skip_the_seed_and_say_why(project):
    with project.lock:
        r = conversations.candidates(project.conn)
    assert r["passed"] == []
    # first conversation: 11 messages but a 79-minute gap; second: 3 messages
    assert r["skipped"]["long_silence"] == 1 and r["skipped"]["too_short"] == 1
    assert sum(r["skipped"].values()) == 2


def test_loose_criteria_pass_and_estimate_matches_export(project):
    with project.lock:
        r = conversations.candidates(project.conn, **LOOSE)
    [c] = r["passed"]
    assert c["id"] == 1 and c["messages"] == 11 and c["people"] == 5 and c["max_gap_ms"] > 30 * 60 * 1000
    assert r["replies"] == c["replies"] > 0 and c["repliers"] >= 2
    with project.tx() as conn:
        conversations.approve_many(conn, [c["id"]])
    built = export.build(project)
    assert sum(a["count"] for a in built["by_author"]) == c["replies"]
    with project.lock:
        after = conversations.candidates(project.conn, **LOOSE)
    assert after["passed"] == [] and after["approved"] == {"conversations": 1, "replies": c["replies"]}


def test_media_and_bots_count_against_a_conversation(project):
    with project.tx() as conn:
        for mid in ("1001", "1004", "1005"):
            conn.execute("UPDATE messages SET content_text = 'https://tenor.com/view/x' WHERE id = ?", (mid,))
    with project.lock:
        r = conversations.candidates(project.conn, **LOOSE)
    assert r["skipped"]["mostly_links"] == 1  # 3 of 11 is over 25%
    with project.lock:
        assert conversations.candidates(project.conn, **LOOSE, max_media=0.5)["passed"]
    with project.tx() as conn:
        conn.execute("UPDATE authors SET is_bot = 1 WHERE id IN ('101', '102', '103')")
    with project.lock:
        r = conversations.candidates(project.conn, **LOOSE, max_media=0.5)
    assert r["skipped"]["few_people"] == 1  # two people left


def test_bulk_approve_then_undo_puts_suggestions_back(project):
    before = project.all("SELECT unit_kind, unit_id, parent_id, status, origin, score FROM links ORDER BY id")
    assert any(l["status"] == "suggested" for l in before)
    with project.tx() as conn:
        out = conversations.approve_many(conn, [1, 2, 999])
    assert out["approved"] == [1, 2] and out["skipped"] == [999] and out["accepted"]
    assert project.scalar("SELECT COUNT(*) FROM links WHERE status = 'suggested'") == 0
    assert project.scalar("SELECT COUNT(*) FROM conversations WHERE approved_at IS NOT NULL") == 2

    with project.tx() as conn:
        again = conversations.approve_many(conn, [1])  # already approved: nothing for undo to touch
    assert again["approved"] == [] and again["accepted"] == []

    with project.tx() as conn:
        back = conversations.unapprove_many(conn, out["approved"], out["accepted"])
    assert back["restored"] == len(out["accepted"]) and sorted(back["unapproved"]) == [1, 2]
    assert project.all("SELECT unit_kind, unit_id, parent_id, status, origin, score FROM links ORDER BY id") == before
    assert project.scalar("SELECT COUNT(*) FROM conversations WHERE approved_at IS NOT NULL") == 0


def test_undo_leaves_an_arrow_changed_since_alone(project):
    with project.tx() as conn:
        out = conversations.approve_many(conn, [1])
        a = out["accepted"][0]
        conn.execute("UPDATE links SET parent_id = '1002' WHERE unit_kind = ? AND unit_id = ?", (a["unit_kind"], a["unit_id"]))
    with project.tx() as conn:
        back = conversations.unapprove_many(conn, [1], out["accepted"])
    assert back["restored"] == len(out["accepted"]) - 1
    row = project.one("SELECT parent_id, status FROM links WHERE unit_kind = ? AND unit_id = ?", (a["unit_kind"], a["unit_id"]))
    assert row == {"parent_id": "1002", "status": "accepted"}


def test_candidates_and_bulk_endpoints(tmp_path):
    p = Project.create(tmp_path / "bulk.ohm", "Bulk")
    seed(p)
    p.save()
    p.close()
    c = TestClient(main.app)
    c.post("/project/open", json={"path": str(tmp_path / "bulk.ohm")})
    try:
        r = c.get("/conversations/candidates").json()
        assert r["candidates"] == [] and r["criteria"] == r["defaults"] and r["reasons"]["too_short"]
        r = c.get("/conversations/candidates", params={"max_gap_ms": HOURS_2, "min_attached": 0}).json()
        [cand] = r["candidates"]
        assert "reasons" not in cand and r["criteria"]["max_gap_ms"] == HOURS_2

        out = c.post("/conversations/approve-bulk", json={"ids": [cand["id"]]}).json()
        assert out["approved"] == [cand["id"]] and out["dirty"]
        assert [x["id"] for x in c.get("/conversations?filter=approved").json()["conversations"]] == [cand["id"]]
        r = c.get("/conversations/candidates", params={"max_gap_ms": HOURS_2, "min_attached": 0}).json()
        assert r["candidates"] == [] and r["approved"]["conversations"] == 1

        back = c.post("/conversations/unapprove-bulk", json={"ids": out["approved"], "accepted": out["accepted"]}).json()
        assert back["unapproved"] == [cand["id"]] and back["restored"] == len(out["accepted"])
        assert c.get("/conversations?filter=approved").json()["conversations"] == []

        # archiving is how the sample's Reject works: it leaves the candidates
        c.post(f"/conversations/{cand['id']}/archive")
        r = c.get("/conversations/candidates", params={"max_gap_ms": HOURS_2, "min_attached": 0}).json()
        assert r["candidates"] == []
        assert json.dumps(r)  # plain JSON all the way down
    finally:
        state.project.close()
        state.project = None
