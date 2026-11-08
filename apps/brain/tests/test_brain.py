import json

import pytest

from brain import Task, last_backend, needs_gpu_swap, pack, run, run_many, run_packed, unpack
from brain import config
from brain.backends import parse_claude, parse_codex, strict
from brain.router import parse


class Fake:
    """A backend that answers from a list (or raises when the answer is an exception)."""

    def __init__(self, name, answers, ready=True, gpu=False, usage=None):
        self.name = name
        self.answers = list(answers)
        self.calls = []
        self._ready = ready
        self.needs_gpu_swap = gpu
        self.last_usage = usage or {}

    def ready(self):
        return self._ready

    def complete(self, task):
        self.calls.append(task)
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a(task) if callable(a) else a


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    for k in list(__import__("os").environ):
        if k.startswith("BRAIN_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("CURATOR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "MEMORY_API_URL", "")
    backends = {}
    monkeypatch.setattr(config, "backend", lambda name: backends.get(name))
    return backends


def test_falls_back_in_order(fakes, monkeypatch):
    monkeypatch.setenv("BRAIN_ROLE_JUDGE", "codex,qwen")
    fakes["codex"] = Fake("codex", [RuntimeError("rate limited")])
    fakes["qwen"] = Fake("qwen", ["fine"])
    assert run(Task("judge", "s", "u")) == "fine"
    assert len(fakes["codex"].calls) == 1 and len(fakes["qwen"].calls) == 1


def test_all_fail_gives_none(fakes, monkeypatch):
    monkeypatch.setenv("BRAIN_ROLE_LABEL", "codex,nonsense")
    fakes["codex"] = Fake("codex", [TimeoutError("slow")])
    assert run(Task("label", "s", "u")) is None


def test_local_only_skips_cloud(fakes, monkeypatch):
    monkeypatch.setenv("BRAIN_ROLE_BRIEF", "codex,local")
    fakes["codex"] = Fake("codex", ["cloud"])
    fakes["local"] = Fake("local", ["local"])
    assert run(Task("brief", "s", "u", local_only=True)) == "local"
    assert not fakes["codex"].calls
    monkeypatch.setenv("BRAIN_LOCAL_ONLY", "1")
    assert run(Task("brief", "s", "u")) == "local"
    assert not fakes["codex"].calls


def test_not_installed_is_skipped(fakes):
    fakes["codex"] = Fake("codex", ["x"], ready=False)
    fakes["local"] = Fake("local", ["local"])
    assert run(Task("curate", "s", "u")) == "local"
    assert not fakes["codex"].calls


def test_schema_parsing_and_shallow_check(fakes):
    schema = {"type": "object", "properties": {"yes": {"type": "boolean"}, "why": {"type": "string"}},
              "required": ["yes"]}
    assert parse('Sure! ```json\n{"yes": true, "why": "ok"}\n```', schema) == {"yes": True, "why": "ok"}
    assert parse('{"why": "no yes"}', schema) is None
    assert parse('{"yes": "true"}', schema) is None
    assert parse("no json here", schema) is None
    assert parse('[1, 2]', {"type": "array"}) == [1, 2]
    assert parse('{"n": true}', {"type": "object", "properties": {"n": {"type": "integer"}}}) is None
    # A backend whose answer doesn't fit falls through to the next one
    fakes["codex"] = Fake("codex", ['{"why": "x"}'])
    fakes["local"] = Fake("local", ['{"yes": false}'])
    assert run(Task("curate", "s", "u", schema=schema)) == {"yes": False}


def test_pack_unpack_alignment_with_a_missing_answer():
    item = {"type": "object", "properties": {"answers": {"type": "boolean"}}, "required": ["answers"]}
    tasks = pack([f"reply {i}" for i in range(7)], "sys", "Does each reply answer?", item, size=3)
    assert [t.items for t in tasks] == [3, 3, 1]
    assert "### 1\nreply 0" in tasks[0].user and "### 3\nreply 2" in tasks[0].user and "### 1\nreply 6" in tasks[2].user
    assert tasks[0].schema["properties"]["answers"]["items"]["required"] == ["n", "answers"]
    # Out of order, one missing, one that doesn't fit, one number out of range
    got = unpack({"answers": [{"n": 3, "answers": True}, {"n": 1, "answers": "maybe"}, {"n": 9, "answers": True}]}, 3, item)
    assert got == [None, None, {"n": 3, "answers": True}]
    # Without numbers, position decides
    assert unpack([{"answers": False}, {"answers": True}], 2, item) == [{"answers": False}, {"answers": True}]
    assert unpack(None, 2) == [None, None]


def test_run_packed_retries_missing_in_smaller_batches(fakes):
    item = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

    def answer(task):
        # Skips the last item of any pack bigger than 2
        n = task.items if task.items <= 2 else task.items - 1
        return json.dumps({"answers": [{"n": i, "ok": True} for i in range(1, n + 1)]})

    fakes["codex"] = Fake("codex", [answer])
    out = run_packed([str(i) for i in range(6)], "s", "Check each.", item, size=6, workers=1)
    assert all(a == {"n": a["n"], "ok": True} for a in out)
    assert [t.items for t in fakes["codex"].calls] == [6, 1]


def test_run_many_keeps_order(fakes):
    fakes["local"] = Fake("local", [lambda t: t.user.upper()])
    assert run_many([Task("extract", "s", w) for w in "abcde"], workers=3) == list("ABCDE")


def test_daily_cap_skips_paid(fakes, monkeypatch):
    monkeypatch.setenv("BRAIN_ROLE_REFLECT", "deepseek,local")
    monkeypatch.setenv("BRAIN_DAILY_USD", "0.01")
    fakes["deepseek"] = Fake("deepseek", ["paid"], usage={"prompt_tokens": 20_000, "output_tokens": 5_000})
    fakes["local"] = Fake("local", ["free"])
    assert run(Task("reflect", "s", "u")) == "paid"
    assert config.spent_today() == pytest.approx((20_000 * 0.27 + 5_000 * 1.10) / 1e6)
    config.add_spend(0.01)
    assert run(Task("reflect", "s", "u")) == "free"
    assert len(fakes["deepseek"].calls) == 1


def test_needs_gpu_swap(fakes, monkeypatch):
    fakes["codex"] = Fake("codex", ["x"])
    fakes["qwen"] = Fake("qwen", ["x"], gpu=True)
    assert needs_gpu_swap("judge") is False
    fakes["codex"]._ready = False
    assert needs_gpu_swap("judge") is True
    fakes["codex"]._ready = True
    monkeypatch.setenv("BRAIN_LOCAL_ONLY", "1")
    assert needs_gpu_swap("judge") is True
    monkeypatch.setenv("BRAIN_ROLE_JUDGE", "local,qwen")
    fakes["local"] = Fake("local", ["x"])
    assert needs_gpu_swap("judge") is False


# Captured from codex-cli 0.163.0: the reply alone on stdout, the transcript and token count on stderr
CODEX_STDERR = """OpenAI Codex v0.163.0
--------
workdir: C:\\Users\\talba\\AppData\\Local\\Temp\\tmpu3b2ohjh
model: gpt-6-astra
reasoning effort: medium
--------
user
Reply with just the word OK.
codex
OK
tokens used
10,291
"""


def test_codex_stdout_parsing(tmp_path):
    assert parse_codex("OK\n", CODEX_STDERR) == ("OK", {"prompt_tokens": 10291, "output_tokens": None})
    # Older builds print the token count after the reply on stdout
    assert parse_codex("Two lines\nof reply\n\ntokens used\n1,234\n") == ("Two lines\nof reply",
                                                                       {"prompt_tokens": 1234, "output_tokens": None})
    out = tmp_path / "out.json"
    out.write_text('{"answers":[{"n":1,"yes":true}]}', encoding="utf-8")
    assert parse_codex('{"answers":[{"n":1,"yes":true}]}\n', CODEX_STDERR, str(out))[0] == '{"answers":[{"n":1,"yes":true}]}'
    with pytest.raises(RuntimeError):
        parse_codex("\n", CODEX_STDERR)


def test_claude_envelope_parsing():
    env = {"type": "result", "is_error": False, "result": '{"yes":true}', "structured_output": {"yes": True},
           "usage": {"input_tokens": 2, "cache_read_input_tokens": 649, "cache_creation_input_tokens": 3035, "output_tokens": 4}}
    assert parse_claude(json.dumps(env)) == ('{"yes": true}', {"prompt_tokens": 3686, "output_tokens": 4})
    with pytest.raises(RuntimeError):
        parse_claude(json.dumps({"is_error": True, "result": "Not logged in"}))


def test_strict_schema_for_codex():
    s = strict({"type": "object", "properties": {"a": {"type": "array", "items": {"type": "object",
                "properties": {"n": {"type": "integer"}, "x": {"type": "string"}}, "required": ["n"]}}}})
    item = s["properties"]["a"]["items"]
    assert s["additionalProperties"] is False and s["required"] == ["a"]
    assert item["required"] == ["n", "x"] and item["additionalProperties"] is False


# ── Images ───────────────────────────────────────────────────────────────────
PNG_URL = "data:image/png;base64,iVBORw0KGgo="


def test_codex_args_with_an_image(tmp_path):
    from brain.backends import CliBackend
    args, stdin = CliBackend("codex")._codex_args(Task("screen", "One line.", "What's on it?", images=[PNG_URL]),
                                                   str(tmp_path))
    image = tmp_path / "image0.png"
    assert args[:3] == ["exec", "-i", str(image)]  # before the other options, so "-" isn't taken as an image
    assert image.read_bytes() == b"\x89PNG\r\n\x1a\n"
    assert args[-1] == "-" and stdin == "One line.\n\nWhat's on it?"
    assert "-i" not in CliBackend("codex")._codex_args(Task("screen", "s", "u"), str(tmp_path))[0]


def test_claude_refuses_images(tmp_path):
    from brain.backends import CliBackend
    with pytest.raises(RuntimeError):
        CliBackend("claude")._claude_args(Task("screen", "s", "u", images=[PNG_URL]), str(tmp_path))


def test_openai_content_parts(tmp_path):
    from brain.backends import OpenAIBackend
    picture = tmp_path / "shot.jpg"
    picture.write_bytes(b"\xff\xd8\xff")
    local = OpenAIBackend("local", "http://127.0.0.1:5000/v1")
    assert local.content(Task("screen", "s", "just text")) == "just text"
    assert local.content(Task("screen", "s", "look", images=[PNG_URL, str(picture)])) == [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": PNG_URL}},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/"}},
    ]
    with pytest.raises(RuntimeError):
        OpenAIBackend("deepseek", "https://api.deepseek.com/v1", key_env="X").content(
            Task("screen", "s", "u", images=[PNG_URL]))


def test_screen_role_and_last_backend(fakes, monkeypatch):
    monkeypatch.delenv("BRAIN_ROLE_SCREEN", raising=False)
    assert config.roles()["screen"] == ["codex"]
    monkeypatch.setenv("BRAIN_ROLE_SCREEN", "local")
    assert config.chain("screen") == ["local"]
    fakes["codex"] = Fake("codex", [RuntimeError("down")])
    fakes["local"] = Fake("local", ["a menu"])
    monkeypatch.setenv("BRAIN_ROLE_SCREEN", "codex,local")
    assert run(Task("screen", "s", "u", images=[PNG_URL])) == "a menu" and last_backend() == "local"
    # A screenshot marked local_only never reaches a cloud backend
    assert run(Task("screen", "s", "u", images=[PNG_URL], local_only=True)) == "a menu"
    assert len(fakes["codex"].calls) == 1
    monkeypatch.setenv("BRAIN_ROLE_SCREEN", "codex")
    assert run(Task("screen", "s", "u")) is None and last_backend() is None
