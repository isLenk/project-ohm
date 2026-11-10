from curator_service.app import conversations, curate


class Job:
    def __init__(self):
        self.notes = []

    def progress(self, done, total, note=None):
        self.notes.append((done, total, note))


def test_curate_approves_only_what_the_model_keeps(project, monkeypatch):
    loose = {"min_messages": 1, "min_people": 1, "max_gap_ms": 10**12, "max_media": 1.0, "min_attached": 0.0,
             "min_repliers": 0}
    with project.lock:
        cands = conversations.candidates(project.conn, **loose)["passed"]
    assert len(cands) >= 2
    seen = []

    def judge(items, system, instructions, schema_item, **kw):
        assert kw["role"] == "curate" and "merged" in system
        seen.extend(items)
        return [{"keep": i == 0, "why": "banter" if i == 0 else "logistics"} for i, _ in enumerate(items)]

    monkeypatch.setattr(curate.brain, "run_packed", judge)
    out = curate.run(project, Job(), loose)
    assert out["read"] == len(cands) and out["approved"] == 1 and out["left_out"] == len(cands) - 1
    assert all(": " in text for text in seen)  # rendered as "Name: text" lines
    with project.lock:
        approved = conversations.listing(project.conn, "approved")
    assert [c["id"] for c in approved] == [cands[0]["id"]]


def test_unanswered_conversations_stay_unapproved(project, monkeypatch):
    loose = {"min_messages": 1, "min_people": 1, "max_gap_ms": 10**12, "max_media": 1.0, "min_attached": 0.0,
             "min_repliers": 0}
    monkeypatch.setattr(curate.brain, "run_packed", lambda items, *a, **kw: [None] * len(items))
    out = curate.run(project, Job(), loose)
    assert out["approved"] == 0 and out["unanswered"] == out["read"]
