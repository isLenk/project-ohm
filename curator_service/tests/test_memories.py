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

    def fake(system, user, max_tokens=300):
        for needle, answer in answers.items():
            if needle in user:
                return answer
        return {"note": None}

    monkeypatch.setattr(llm, "up", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake)
    result = memories.extract(project, FakeJob())
    assert result["created"] == 2  # the 3-line block yields one note; the mass ping another

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
