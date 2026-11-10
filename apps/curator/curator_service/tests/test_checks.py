"""The judge checking trained replies (train/checks.py), with a stand-in for the brain's judge."""

import brain
import pytest

from curator_service.app import conversations, export
from curator_service.app.train import checks, evaluate


class FakeJob:
    cancelled = False

    def __init__(self):
        self.notes = []

    def progress(self, done, total, note=None):
        self.notes.append((done, total, note))


def _reply(text: str) -> str:
    """The candidate in a judge prompt (the chat before it can hold the same words)."""
    return text.rsplit("A:\n", 1)[1]


@pytest.fixture
def judge(monkeypatch):
    """A Codex-like judge: no GPU swap. Fails the test if anything touches llama-server."""
    calls = []
    monkeypatch.setattr(brain, "needs_gpu_swap", lambda role: False)
    monkeypatch.setattr(evaluate, "judge_name", lambda: "codex")
    monkeypatch.setattr(evaluate, "_start_judge", lambda: (_ for _ in ()).throw(AssertionError("started the judge")))
    for name in ("running_llama", "stop", "start"):
        monkeypatch.setattr(checks.gpu, name, lambda *a, _n=name: (_ for _ in ()).throw(AssertionError(_n)))

    def use(answer):
        def fake(items, system, instructions, schema_item, size=30, workers=3, **fields):
            calls.append({"n": len(items), "size": size, "workers": workers, **fields})
            return [answer(t) for t in items]
        monkeypatch.setattr(brain, "run_packed", fake)
        return calls
    return use


def test_judge_verdicts_leave_non_answers_out_of_training(project, judge, monkeypatch):
    monkeypatch.setattr(export, "MAX_REPLY_LINES", 4)  # "Mb I was out" is 3 lines
    with project.tx() as conn:
        conversations.approve(conn, 1, confirm=True)
    before = export.build(project)
    todo = checks.pending(project)
    assert len(todo) == len({it["key"] for it in todo}) == before["checked"]["unchecked"] > 0
    assert before["checked"]["checked"] == 0
    # Each turn is checked against what came before it, the way it's trained
    late = next(it for it in todo if it["reply"].startswith("Mb I was out"))
    assert late["context"][-1]["content"].endswith("we running barotrauma this night? @Ohm")

    calls = judge(lambda t: {"n": 1, "answers": not _reply(t).startswith("Mb I was out"), "personal": False})
    job = FakeJob()
    counts = checks.run(project, None, todo, job)
    assert counts == {"answers": len(todo) - 1, "does_not": 1, "personal": 0, "unreadable": 0}
    assert job.notes[-1][:2] == (len(todo), len(todo))
    assert calls[0]["role"] == "judge" and calls[0]["size"] == 30 and calls[0]["workers"] == 3

    after = export.build(project)
    assert after["dropped"]["doesn't answer (judge)"] == 1
    assert not any(m["content"].startswith("Mb I was out") for e in after["examples"] for m in e["messages"])
    assert after["checked"] == {"checked": before["checked"]["unchecked"] - 1, "unchecked": 0}
    assert checks.pending(project) == []  # nothing is checked twice
    with project.lock:
        assert {r[0] for r in project.conn.execute("SELECT judge FROM reply_checks")} == {"codex"}


def test_unreadable_verdicts_are_left_to_check_again(project, judge):
    with project.tx() as conn:
        conversations.approve(conn, 1, confirm=True)
    todo = checks.pending(project)
    judge(lambda t: None)
    counts = checks.run(project, None, todo, FakeJob())
    assert counts["unreadable"] == len(todo)
    assert len(checks.pending(project)) == len(todo)


def test_qwen_judge_swaps_the_gpu(project, monkeypatch):
    with project.tx() as conn:
        conversations.approve(conn, 1, confirm=True)
    todo = checks.pending(project)
    seen = []
    monkeypatch.setattr(brain, "needs_gpu_swap", lambda role: True)
    monkeypatch.setattr(brain, "run_packed", lambda items, *a, **k: [{"answers": True, "personal": False}
                                                                     for _ in items])
    monkeypatch.setattr(checks.gpu, "running_llama", lambda: {"pid": 1, "cmdline": ["llama-server"]})
    monkeypatch.setattr(checks.gpu, "stop", lambda pids: seen.append(("stop", pids)))
    monkeypatch.setattr(evaluate, "_start_judge", lambda: seen.append("judge") or 99)
    monkeypatch.setattr(evaluate, "_restore", lambda before, reg: seen.append(("restore", before["pid"])))
    assert checks.run(project, None, todo, FakeJob())["answers"] == len(todo)
    assert seen == [("stop", [1]), "judge", ("stop", [99]), ("restore", 1)]


def test_nothing_to_check_doesnt_start_the_judge(project, monkeypatch):
    monkeypatch.setattr(evaluate, "_start_judge", lambda: (_ for _ in ()).throw(AssertionError("started")))
    assert checks.run(project, None, [], FakeJob()) == {"answers": 0, "does_not": 0, "personal": 0, "unreadable": 0}


def test_personal_replies_are_left_out_and_old_checks_asked_again(project, judge):
    with project.tx() as conn:
        conversations.approve(conn, 1, confirm=True)
    todo = checks.pending(project)
    first = todo[0]["reply"]
    # An old verdict without "personal" is asked again
    with project.tx() as conn:
        conn.execute("INSERT INTO reply_checks (reply_ids, answers, judge, checked_at) VALUES (?, 1, 'codex', 0)",
                     (todo[0]["key"],))
    assert len(checks.pending(project)) == len(todo)
    judge(lambda t: {"n": 1, "answers": True, "personal": _reply(t) == first})
    counts = checks.run(project, None, checks.pending(project), FakeJob())
    assert counts["personal"] == 1 and counts["answers"] == len(todo)
    after = export.build(project)
    assert after["dropped"]["about their own life (judge)"] == 1
    assert not any(m["content"] == first for e in after["examples"] for m in e["messages"] if m["role"] == "assistant")
    assert checks.pending(project) == []
    with project.lock:
        assert project.conn.execute("SELECT COUNT(*) FROM reply_checks WHERE personal IS NULL").fetchone()[0] == 0


def test_no_judge_is_a_409(monkeypatch):
    from curator_service.app.errors import ApiError
    monkeypatch.setattr(brain, "needs_gpu_swap", lambda role: False)
    monkeypatch.setattr(evaluate.llm, "brain_backend", lambda role: None)
    with pytest.raises(ApiError) as e:
        evaluate.check_judge()
    assert e.value.code == "no_judge" and "Codex" in e.value.message
    monkeypatch.setattr(evaluate.llm, "brain_backend", lambda role: "codex")
    monkeypatch.setattr(brain, "status", lambda: {"codex": {"signed_in": False}})
    with pytest.raises(ApiError):
        evaluate.check_judge()
    monkeypatch.setattr(brain, "status", lambda: {"codex": {"signed_in": True}})
    evaluate.check_judge()
