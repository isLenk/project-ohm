"""The judge checking trained replies (train/checks.py), with a stand-in for the judge model."""

from curator_service.app import conversations, export
from curator_service.app.train import checks, evaluate


class FakeJob:
    cancelled = False

    def __init__(self):
        self.notes = []

    def progress(self, done, total, note=None):
        self.notes.append((done, total, note))


def test_judge_verdicts_leave_non_answers_out_of_training(project, monkeypatch):
    with project.tx() as conn:
        conversations.approve(conn, 1, confirm=True)
    before = export.build(project)
    todo = checks.pending(project)
    assert len(todo) == len({it["key"] for it in todo}) == before["checked"]["unchecked"] > 0
    assert before["checked"]["checked"] == 0
    # Each turn is checked against what came before it, the way it's trained
    late = next(it for it in todo if it["reply"].startswith("Mb I was out"))
    assert late["context"][-1]["content"].endswith("we running barotrauma this night? @Ohm")

    monkeypatch.setattr(checks.gpu, "running_llama", lambda: None)
    monkeypatch.setattr(evaluate, "_start_judge", lambda: None)
    job = FakeJob()
    counts = checks.run(project, None, todo, job, judge_reply=lambda it: {"A": not it["reply"].startswith("Mb I was out")})
    assert counts == {"answers": len(todo) - 1, "does_not": 1, "unreadable": 0}
    assert job.notes[-1][:2] == (len(todo), len(todo))

    after = export.build(project)
    assert after["dropped"]["doesn't answer (judge)"] == 1
    assert not any(m["content"].startswith("Mb I was out") for e in after["examples"] for m in e["messages"])
    assert after["checked"] == {"checked": before["checked"]["unchecked"] - 1, "unchecked": 0}
    assert checks.pending(project) == []  # nothing is checked twice


def test_unreadable_verdicts_are_left_to_check_again(project, monkeypatch):
    with project.tx() as conn:
        conversations.approve(conn, 1, confirm=True)
    todo = checks.pending(project)
    monkeypatch.setattr(checks.gpu, "running_llama", lambda: None)
    monkeypatch.setattr(evaluate, "_start_judge", lambda: None)
    counts = checks.run(project, None, todo, FakeJob(), judge_reply=lambda it: None)
    assert counts["unreadable"] == len(todo)
    assert len(checks.pending(project)) == len(todo)


def test_nothing_to_check_doesnt_start_the_judge(project, monkeypatch):
    monkeypatch.setattr(evaluate, "_start_judge", lambda: (_ for _ in ()).throw(AssertionError("started")))
    assert checks.run(project, None, [], FakeJob()) == {"answers": 0, "does_not": 0, "unreadable": 0}
