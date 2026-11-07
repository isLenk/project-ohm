import re

import requests

from curator_service.app import conversations, llm, model_links, threads


class FakeJob:
    cancelled = False

    def progress(self, *a, **k):
        pass


def block_of(project, message_id: str) -> str:
    return str(project.scalar("SELECT block_id FROM messages WHERE id = ?", (message_id,)))


def choice_number(user: str, needle: str):
    for line in user.splitlines():
        m = re.match(r"\[(\d+)\] ", line)
        if m and needle in line:
            return int(m.group(1))
    return None


def fake_model(system, user, max_tokens=300):
    """'bet' answers Kaptain's "down to baro"; 'same' answers "im in"; everything else starts fresh."""
    new = user.split("New message from ", 1)[1]
    if new.endswith("\nbet"):
        return {"reply_to": choice_number(user, "down to baro")}
    if new.endswith("\nsame"):
        return {"reply_to": str(choice_number(user, "im in, give me 5"))}  # strings are fine too
    return {"reply_to": None}


def test_pick_rejects_malformed_answers():
    assert model_links.pick({"reply_to": 2}, 3) == 1
    assert model_links.pick({"reply_to": "3"}, 3) == 2
    for bad in (None, [], {"reply_to": None}, {"reply_to": 0}, {"reply_to": 4}, {"reply_to": True}, {"x": 1}):
        assert model_links.pick(bad, 3) is None


def test_guess_suggests_from_model(project, monkeypatch):
    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake_model)
    result = model_links.guess(project, FakeJob())
    assert result["blocks"] == 4 and result["suggested"] == 2 and result["no_reply"] == 2
    assert result["units"] == []  # project-wide runs don't send units back; the UI reloads the feed
    assert {r["unit_id"] for r in project.all("SELECT unit_id FROM links WHERE origin = 'model'")} == {
        block_of(project, "1009"), block_of(project, "1012")}

    bet = threads.link_row(project.conn, "block", block_of(project, "1009"))
    assert bet["parent_id"] == "1007" and bet["status"] == "suggested" and bet["origin"] == "model"
    same = threads.link_row(project.conn, "block", block_of(project, "1012"))
    assert same["parent_id"] == "1011" and same["origin"] == "model"

    # the model has seen everything; a second run asks nothing
    assert model_links.guess(project, FakeJob())["blocks"] == 0


def test_guess_skips_approved_and_scopes_to_conversation(project, monkeypatch):
    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake_model)
    with project.tx() as conn:
        conversations.approve(conn, 2, confirm=True)
    assert model_links.guess(project, FakeJob(), conversation_id=2)["blocks"] == 0
    assert model_links.guess(project, FakeJob(), conversation_id=1)["blocks"] == 2


def test_failed_requests_are_retried(project, monkeypatch):
    monkeypatch.setattr(llm, "up", lambda: True)

    def broken(*a, **k):
        raise requests.Timeout("slow")

    monkeypatch.setattr(llm, "chat_json", broken)
    r = model_links.guess(project, FakeJob())
    assert r["failed"] == 4 and r["suggested"] == 0
    assert project.scalar("SELECT COUNT(*) FROM model_link_scans") == 0

    monkeypatch.setattr(llm, "chat_json", fake_model)
    assert model_links.guess(project, FakeJob())["suggested"] == 2


def test_guess_stops_when_server_dies(project, monkeypatch):
    alive = iter([True, False])
    monkeypatch.setattr(llm, "up", lambda: next(alive, False))
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError()))
    r = model_links.guess(project, FakeJob())
    assert r["stopped"] and r["failed"] == 0
    assert project.scalar("SELECT COUNT(*) FROM model_link_scans") == 0


def test_rejecting_model_guess_counts_for_model(project, monkeypatch):
    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake_model)
    model_links.guess(project, FakeJob())
    with project.tx() as conn:
        threads.reject(conn, "block", block_of(project, "1009"), project.settings())
        threads.accept(conn, "block", block_of(project, "1012"))
    stats = threads.suggester_stats(project.conn)
    assert stats["model"] == {"accepted": 1, "rejected": 1, "pending": 0, "precision": 0.5}
    assert stats["rejected"] == 0


def test_approving_accepts_model_guesses_without_asking(project, monkeypatch):
    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake_model)
    model_links.guess(project, FakeJob())
    bid = block_of(project, "1012")
    cid = project.scalar("SELECT id FROM conversations WHERE first_ts <= (SELECT first_ts FROM blocks WHERE id = ?) "
                         "ORDER BY first_ts DESC LIMIT 1", (int(bid),))
    with project.tx() as conn:
        conn.execute("DELETE FROM links WHERE status = 'root'")  # leave only predictions open
        for u in conversations.units_in(conn, conversations.get(conn, cid)):
            if not u["link"]:
                threads._write(conn, u["kind"], u["id"], None, "root", "manual")
        s = conversations.approve(conn, cid, confirm=False)  # no 409: predictions don't need a yes
    assert s["approved"] and s["suggested"] == 0
    assert threads.link_row(project.conn, "block", bid)["status"] == "accepted"


def test_guess_goes_newest_first(project):
    pending = model_links._pending(project, None)
    ts = [project.scalar("SELECT first_ts FROM blocks WHERE id = ?", (b,)) for b in pending]
    assert len(pending) > 1 and ts == sorted(ts, reverse=True)

