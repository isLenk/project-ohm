import requests

from curator_service.app import llm, memories


class FakeJob:
    cancelled = False

    def progress(self, *a, **k):
        pass


def test_extract_with_fake_llm(project, monkeypatch):
    answers = {
        "I’m down to baro": {"note": "KaptainDoosh plays Barotrauma with the group.", "category": "fact", "short_lived": False},
        "baro with me and arnav": {"note": "KaptainDoosh plays with someone called arnav.", "category": "relationship",
                                   "short_lived": False, "mentions_person": "arnav"},
        "Mb I was out": {"note": "KaptainDoosh was out all day.", "category": "fact", "short_lived": True},
    }

    prompts = []

    def fake(system, user, max_tokens=300):
        prompts.append(user)
        said = user.split("said:\n", 1)[1]  # match only their own messages, not the context before them
        for needle, answer in answers.items():
            if needle in said:
                return answer
        return {"note": None}

    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake)
    result = memories.extract(project, FakeJob())
    assert result["created"] == 2  # the 3-line block yields one note; the mass ping another
    assert all("Context, earlier in the chat:" in u for u in prompts)

    with project.lock:
        mems = memories.listing(project.conn, "100", "new")
    texts = {m["text"]: m for m in mems}
    assert "KaptainDoosh plays with someone called arnav." in texts
    arnav = texts["KaptainDoosh plays with someone called arnav."]
    assert arnav["category"] == "relationship" and arnav["mentioned_name"] == "arnav" and arnav["sources"]

    # running again scans nothing new
    assert memories.extract(project, FakeJob())["blocks"] == 0

    with project.tx() as conn:
        kept = memories.keep_all(conn, "100")
    assert kept >= 1
    with project.lock:
        assert all(not m["short_lived"] for m in memories.listing(project.conn, "100", "kept"))


def test_sync_needs_memory_service(project):
    import pytest

    from curator_service.app.errors import ApiError

    with pytest.raises(ApiError) as e:
        with project.tx() as conn:
            memories.sync(conn, "ohm")
    assert e.value.code == "memory_service_off"


def test_extract_leaves_failed_blocks_unscanned(project, monkeypatch):
    monkeypatch.setattr(llm, "up", lambda: True)

    def broken(*a, **k):
        raise requests.Timeout("slow")

    monkeypatch.setattr(llm, "chat_json", broken)
    r = memories.extract(project, FakeJob())
    assert r["failed"] > 0 and r["created"] == 0
    scanned = project.scalar("SELECT COUNT(*) FROM memory_scans")
    assert scanned == r["blocks"] - r["failed"]  # only the too-short blocks


def test_vague_notes_are_dropped(project, monkeypatch):
    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"note": "KaptainDoosh is down to participate.", "category": "fact"})
    assert memories.extract(project, FakeJob())["created"] == 0


def test_extract_reads_newest_blocks_first(project, monkeypatch):
    monkeypatch.setattr(llm, "up", lambda: True)
    seen = []
    monkeypatch.setattr(llm, "chat_json", lambda s, u, **k: seen.append(u.split("said:\n", 1)[1]) or {"note": None})
    memories.extract(project, FakeJob())

    blocks = project.all(
        "SELECT b.id FROM blocks b JOIN authors a ON a.id = b.author_id WHERE a.is_bot = 0 ORDER BY b.first_ts DESC, b.id DESC"
    )
    texts = []
    for b in blocks:
        msgs = project.all("SELECT content_text FROM messages WHERE block_id = ? AND excluded = 0 ORDER BY ts", (b["id"],))
        text = "\n".join(m["content_text"] for m in msgs)
        if len(text.split()) >= memories.MIN_WORDS:
            texts.append(text)
    assert seen == texts
