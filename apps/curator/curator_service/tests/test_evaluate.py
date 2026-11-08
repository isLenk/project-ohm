"""Scoring an adapter with a judge model (train/evaluate.py): the parts that don't need a GPU."""

import json

import pytest

from curator_service.app.errors import ApiError
from curator_service.app.train import evaluate
from curator_service.app.train.worker import held_out

SYSTEM = {"role": "system", "content": "You are Ohm."}


def _rows(convs: int) -> list[dict]:
    return [{"id": f"conv:{c}:0:1:0", "conversation_id": c, "speaker": "Kaptain", "messages": [
        SYSTEM,
        {"role": "user", "content": f"Lenk: you on tonight? {c}\nmargay: same q"},
        {"role": "assistant", "content": "ya after dinner"},
        {"role": "user", "content": "Lenk: bet"},
        {"role": "assistant", "content": f"pulling up {c}"},
    ]} for c in range(convs)]


def _write(tmp_path, rows):
    (tmp_path / "dataset.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_items_are_the_held_out_last_turns_then_probes(tmp_path):
    rows = _rows(30)
    _write(tmp_path, rows)
    out = evaluate.items(tmp_path, 1337)
    held = held_out(rows, 1337)
    scored = [it for it in out if it["kind"] == "held_out"]
    assert len(scored) == len(held) and {int(it["real"].split()[-1]) for it in scored} == held
    assert all(it["context"][-1]["content"] == "Lenk: bet" for it in scored)
    probes = [it for it in out if it["kind"] == "probe"]
    assert len(probes) == len(evaluate.PROBES)
    # Probes are asked by whoever talks most in the held-out chat, under the same system prompt
    assert probes[0]["context"] == [SYSTEM, {"role": "user", "content": f"Lenk: {evaluate.PROBES[0]}"}]


def test_items_need_held_out_conversations(tmp_path):
    _write(tmp_path, _rows(5))
    with pytest.raises(ApiError):
        evaluate.items(tmp_path, 1337)


def test_transcript_labels_ohm_and_keeps_the_end():
    chat = evaluate.transcript(_rows(1)[0]["messages"][:-1])
    assert chat.splitlines() == ["Lenk: you on tonight? 0", "margay: same q", "Ohm: ya after dinner", "Lenk: bet"]
    long = [SYSTEM, {"role": "user", "content": "\n".join(f"Lenk: line {i}" for i in range(2000))}]
    text = evaluate.transcript(long)
    assert len(text) <= evaluate.CONTEXT_CHARS and text.endswith("Lenk: line 1999") and text.startswith("Lenk: ")


def test_parse_answers_and_pick():
    good = 'sure: {"answers": {"A": true, "B": false}}'
    assert evaluate.parse_answers(good, ["A", "B"]) == {"A": True, "B": False}
    assert evaluate.parse_answers('{"answers": {"A": "yes"}}', ["A"]) is None
    assert evaluate.parse_answers("no json here", ["A"]) is None
    assert evaluate.parse_pick('{"real": "B"}', ["A", "B"]) == "B"
    assert evaluate.parse_pick('{"real": "C"}', ["A", "B"]) is None


def test_judge_item_unshuffles_the_labels():
    it = {"context": _rows(1)[0]["messages"][:-1], "real": "pulling up 0",
          "replies": {"base": "As an AI I can't drive.", "adapter": "omw", "partial": "on my way"}}

    def ask(system, prompt):
        # A judge that knows the real reply and says only it and "omw" answer
        labels = dict(block.split(":\n", 1) for block in prompt.split("\n\n")[2:])
        if system == evaluate.JUDGE_SYSTEM:
            return json.dumps({"answers": {k: t in ("pulling up 0", "omw") for k, t in labels.items()}})
        fake = next(k for k, t in labels.items() if t != "pulling up 0")
        return json.dumps({"real": fake if labels[fake] == "omw" else next(k for k in labels if k != fake)})

    evaluate.judge_item(it, 3, ask)
    assert it["answers"] == {"base": False, "adapter": True, "partial": False, "real": True}
    assert it["passes"] == {"base": False, "adapter": True, "partial": False}


def test_summarize():
    results = [
        {"kind": "held_out", "answers": {"base": False, "adapter": True, "partial": True, "real": True},
         "passes": {"base": False, "adapter": True, "partial": None}},
        {"kind": "held_out", "answers": {"base": True, "adapter": False, "partial": True, "real": True},
         "passes": {"base": False, "adapter": False, "partial": True}},
        {"kind": "probe", "answers": {"base": True, "adapter": True, "partial": True}},
        {"kind": "held_out", "answers": None},
    ]
    s = evaluate.summarize(results)
    held = s["held_out"]
    assert held["items"] == 2 and held["skipped"] == 1
    assert held["variants"]["adapter"] == {"answers": 0.5, "n": 2, "passes_as_real": 0.5}
    assert held["variants"]["base"]["passes_as_real"] == 0.0
    assert held["variants"]["partial"]["passes_as_real"] == 1.0  # the unparsed pick doesn't count
    assert "passes_as_real" not in held["variants"]["real"]
    assert s["probe"]["variants"]["adapter"] == {"answers": 1.0, "n": 1} and "real" not in s["probe"]["variants"]


def test_the_judge_server_is_never_taken_for_ohms():
    from curator_service.app import gpu
    from curator_service.app.config import JUDGE_PORT
    assert gpu.is_judge(["llama-server", "-m", "qwen.gguf", "--port", str(JUDGE_PORT)])
    assert not gpu.is_judge(["llama-server", "-m", "gemma.gguf", "--port", "5000"])
    assert not gpu.is_judge(["llama-server", "--port"])


def _labels(prompt: str) -> dict:
    return dict(block.split(":\n", 1) for block in prompt.split("Candidate replies from Ohm:\n\n", 1)[1].split("\n\n"))


def _packed_judge(monkeypatch, calls):
    """A judge that says only the real reply and "omw" answer, and always spots the real one."""
    def fake(items, system, instructions, schema_item, size=30, workers=3, **fields):
        calls.append({"system": system, "n": len(items), "size": size, "workers": workers, **fields})
        out = []
        for t in items:
            labels = _labels(t)
            if system == evaluate.JUDGE_RULES:
                out.append({"n": 1, "answers": [labels[k] in ("omw", "the real one") for k in sorted(labels)]})
            else:
                out.append({"n": 1, "real": next(k for k, v in labels.items() if v == "the real one")})
        return out
    monkeypatch.setattr(evaluate.brain, "run_packed", fake)


def test_judge_all_packs_and_unshuffles(monkeypatch):
    calls = []
    _packed_judge(monkeypatch, calls)
    todo = [{"context": _rows(1)[0]["messages"][:-1], "real": "the real one" if i % 2 == 0 else None,
             "replies": {"base": "As an AI I can't drive.", "adapter": "omw", "partial": "on my way"}} for i in range(70)]
    job = type("J", (), {"cancelled": False, "notes": [], "progress": lambda self, *a: self.notes.append(a)})()
    evaluate.judge_all(todo, job, 0, 1000)
    assert all(it["answers"] == {"base": False, "adapter": True, "partial": False, **({"real": True} if it["real"] else {})}
               for it in todo)
    assert all(it["passes"] == {"base": False, "adapter": False, "partial": False} for it in todo if it["real"])
    assert "passes" not in todo[1]
    # 70 chats go 90 at a time (30 a call, 3 calls at once), then 35 * 3 pairs the same way
    assert [c["n"] for c in calls] == [70, 90, 15] and all(c["size"] == 30 and c["role"] == "judge" for c in calls)
    assert [n[0] for n in job.notes] == [70, 160, 175]


def test_packed_answers_must_fit_the_labels():
    assert evaluate.answers_of({"answers": [True, False]}, ["A", "B"]) == {"A": True, "B": False}
    assert evaluate.answers_of({"answers": [True]}, ["A", "B"]) is None
    assert evaluate.answers_of(None, ["A"]) is None
    assert evaluate.pick_of({"real": "B"}, ["A", "B"]) == "B" and evaluate.pick_of(None, ["A"]) is None


def test_codex_judge_leaves_ohms_llama_server_alone(tmp_path, monkeypatch):
    calls = []
    _packed_judge(monkeypatch, calls)
    ohm = {"pid": 5, "cmdline": ["llama-server", "-m", "gemma.gguf"]}
    monkeypatch.setattr(evaluate.brain, "needs_gpu_swap", lambda role: False)
    monkeypatch.setattr(evaluate, "judge_name", lambda: "codex")
    monkeypatch.setattr(evaluate, "_ohm_server", lambda reg, base, adapter: {"ids": [0], "me": 0})
    monkeypatch.setattr(evaluate, "_reply", lambda context, lora, seed: "omw")
    monkeypatch.setattr(evaluate.gpu, "running_llama", lambda: ohm)
    monkeypatch.setattr(evaluate.gpu, "stop", lambda pids: (_ for _ in ()).throw(AssertionError("stopped llama-server")))
    monkeypatch.setattr(evaluate, "_start_judge", lambda: (_ for _ in ()).throw(AssertionError("started the judge")))
    todo = [{"kind": "held_out", "speaker": "Kaptain", "context": _rows(1)[0]["messages"][:-1], "real": "the real one"}]
    prep = {"adapter": {"id": "a1", "name": "LoRA"}, "row": {"id": "r1"}, "run_dir": tmp_path, "base": {}, "todo": todo}
    job = type("J", (), {"cancelled": False, "progress": lambda self, *a: None})()
    out = evaluate.run(None, prep, job)
    saved = evaluate.load(tmp_path)
    assert saved["judge"] == "codex" and out["summary"] == saved["summary"]
    assert saved["summary"]["held_out"]["variants"]["adapter"] == {"answers": 1.0, "n": 1, "passes_as_real": 0.0}
