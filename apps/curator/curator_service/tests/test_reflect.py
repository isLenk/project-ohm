"""The reflection worker (reflect.py), with a fake NAS and a canned brain answer."""

import json

import brain
import pytest
import requests

from curator_service.app import reflect

MESSAGES = [
    {"id": 11, "role": "user", "user_id": "1", "name": "Kyle", "content": "got the job interview tomorrow",
     "source": "discord", "channel_id": "c1", "created_at": "2026-11-06T20:00:00+00:00"},
    {"id": 12, "role": "assistant", "user_id": None, "name": None, "content": "they'll hire you out of pity",
     "source": "discord", "channel_id": "c1", "created_at": "2026-11-06T20:01:00+00:00"},
    {"id": 13, "role": "user", "user_id": "2", "name": "Lance", "content": "lmaooo",
     "source": "voice", "channel_id": "v1", "created_at": "2026-11-06T20:02:00+00:00"},
    {"id": 14, "role": "user", "user_id": "1", "name": "Kyle", "content": "back",
     "source": "discord", "channel_id": "c1", "created_at": "2026-11-06T21:30:00+00:00"},
]
USERS = [{"user_id": "1", "name": "Kyle", "display_name": "kyle_k", "aliases": []},
         {"user_id": "2", "name": "Lance", "username": "lancet", "aliases": ["Grandpa"]}]
TRAITS = {"traits": [{"trait": "sarcastic", "value": 0.6, "floor": 0.2, "ceiling": 0.9},
                     {"trait": "wholesome", "value": 0.5, "floor": 0.2, "ceiling": 0.9},
                     {"trait": "blunt", "value": 0.89, "floor": 0.2, "ceiling": 0.9}],
          "history": [{"trait": "sarcastic", "value": 0.5, "reason": "old"},
                      {"trait": "sarcastic", "value": 0.58, "reason": "later"}]}


def line(traits, reception):
    return {"traits": traits, "reception": reception, "quote": "..."}


ANSWER = {
    "topics": [{"text": f"ask Kyle how job interview {i} went", "people": ["kyle"], "importance": 0.7} for i in range(7)],
    "jokes": [{"text": "'the blue one': Lance picked the wrong door three times", "people": ["Grandpa", "Nobody"]},
              {"text": "an old joke", "people": []}],
    "opinions": [{"text": "Ohm thinks Hollow Knight is overrated", "importance": 5}],
    "questions": [{"text": "what Kyle does for work", "people": ["Kyle"]}],
    "relationships": [{"person": "Lance", "affinity_delta": 0.4, "notes": "calls him grandpa; the door thing"},
                      {"person": "Kyle", "affinity_delta": -0.05, "notes": ""},
                      {"person": "Ohm", "affinity_delta": 0.1, "notes": "himself"}],
    "ohm_lines": [line(["sarcastic", "blunt"], 0.9), line(["sarcastic", "blunt"], 0.8), line(["Sarcastic", "blunt"], 0.7),
                  line(["wholesome"], 0.0), line(["wholesome"], 0.2)],
    "summary": "Kyle has an interview; Ohm roasted him.",
}


def fresh():
    return json.loads(json.dumps(ANSWER))


class Nas:
    """(method, path) -> handler(params, body) -> JSON. Records every call."""

    def __init__(self):
        self.calls = []
        self.routes = {
            ("GET", "/reflections/due"): lambda p, b: {"id": 5, "character": "ohm"},
            ("POST", "/reflections/5/claim"): lambda p, b: {"id": 5, "character": "ohm", "after_id": 10, "before_id": 15},
            # two at a time, to check the paging
            ("GET", "/messages"): lambda p, b: [m for m in MESSAGES if p["after_id"] < m["id"] < p["before_id"]][:2],
            ("GET", "/users"): lambda p, b: USERS,
            ("GET", "/relationships"): lambda p, b: [{"user_id": "2", "name": "Lance", "affinity": 0.2,
                                                      "notes": "always late"}],
            ("GET", "/traits"): lambda p, b: TRAITS,
            ("GET", "/characters/ohm"): lambda p, b: {"name": "Ohm", "personality": "A sarcastic robot. " * 100},
            ("POST", "/memories/search"): lambda p, b: ([{"kind": "joke", "similarity": 0.95}] if b["text"] == "an old joke"
                                                        else [{"kind": "topic", "similarity": 0.99}]),
            ("POST", "/memories"): lambda p, b: {"id": 1, **b},
            ("PATCH", "/relationships/ohm/2"): lambda p, b: {},
            ("PATCH", "/relationships/ohm/1"): lambda p, b: {},
            ("PUT", "/traits/ohm"): lambda p, b: {},
            ("GET", "/memories"): lambda p, b: [{"id": 70, "kind": "self", "content": "old note"}],
            ("DELETE", "/memories/70"): lambda p, b: {"deleted": 70},
            ("POST", "/reflections/5/finish"): lambda p, b: {},
        }

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        assert headers and "X-API-Key" in headers
        path = url[len("http://nas"):]
        self.calls.append((method, path, params, json))
        handler = self.routes.get((method, path))
        r = requests.Response()
        r.status_code = 200 if handler else 404
        r._content = _dumps(handler(params, json) if handler else {"detail": "Not found"})
        r.url = url
        return r

    def bodies(self, method, path):
        return [c[3] for c in self.calls if c[0] == method and c[1] == path]


def _dumps(value) -> bytes:
    return json.dumps(value).encode()


@pytest.fixture
def nas(monkeypatch):
    fake = Nas()
    monkeypatch.setattr(reflect, "MEMORY_API_URL", "http://nas")
    monkeypatch.setattr(reflect.requests, "request", fake.request)
    monkeypatch.setattr(reflect.llm, "brain_backend", lambda role: "codex")
    return fake


@pytest.fixture
def think(monkeypatch):
    tasks = []

    def use(answer):
        monkeypatch.setattr(brain, "run", lambda task: tasks.append(task) or answer)
        return tasks
    return use


def test_a_reflection_writes_everything(nas, think, monkeypatch):
    monkeypatch.setattr(reflect, "PAGE", 2)  # the fake NAS gives two at a time
    tasks = think(fresh())
    assert reflect.poll_once()["id"] == 5

    task = tasks[0]
    assert task.role == "reflect" and task.reasoning == "high" and task.schema == reflect.SCHEMA
    assert "A sarcastic robot." in task.system and "NAME" not in task.system
    assert task.system.count("A sarcastic robot.") == reflect.PERSONALITY_CHARS // len("A sarcastic robot. ")  # cut at 600
    assert "sarcastic 0.60" in task.system and "- Lance: +0.20, always late" in task.system
    # Paged, grouped by channel, and a time marker after the 88-minute gap
    chat = task.user.split("\n\n", 1)[1]
    assert chat.index("Kyle: got the job") < chat.index("Ohm: they'll hire") < chat.index("Kyle: back") < chat.index("### voice")
    assert chat.count("\n(") == 3 and "Lance: lmaooo" in chat
    assert [c[2]["after_id"] for c in nas.calls if c[1] == "/messages"] == [10, 12, 14]

    posted = nas.bodies("POST", "/memories")
    kinds = [m["kind"] for m in posted]
    # every topic is near-identical to one there already, and one joke is too
    assert kinds.count("topic") == 0 and kinds.count("joke") == 1 and kinds.count("opinion") == 1
    assert kinds.count("question") == 1 and kinds.count("self") == 1
    joke = next(m for m in posted if m["kind"] == "joke")
    assert joke["user_ids"] == ["2"] and joke["importance"] == reflect.DEFAULT_IMPORTANCE  # by alias; unknown name dropped
    assert next(m for m in posted if m["kind"] == "opinion")["importance"] == 1.0
    assert next(m for m in posted if m["kind"] == "question")["user_ids"] == ["1"]

    assert nas.bodies("PATCH", "/relationships/ohm/2") == [
        {"affinity_delta": reflect.MAX_AFFINITY_STEP, "notes": "always late; calls him grandpa; the door thing"}]
    assert nas.bodies("PATCH", "/relationships/ohm/1") == [{"affinity_delta": -0.05}]
    assert len([c for c in nas.calls if c[0] == "PATCH"]) == 2  # nothing for Ohm himself

    traits = {t["trait"]: t for t in nas.bodies("PUT", "/traits/ohm")[0]["traits"]}
    assert set(traits) == {"sarcastic", "blunt"}  # wholesome only showed in 2 lines
    assert traits["sarcastic"]["value"] == pytest.approx(0.6 + 0.03 * (0.8 - 0.5) * 2, abs=1e-3)
    assert traits["sarcastic"]["reason"] == "3 sarcastic lines, reception 0.80"
    assert traits["blunt"]["value"] == 0.9  # clamped to the ceiling

    # sarcastic went 0.5 -> 0.618 over the month; blunt 0.89 -> 0.9 isn't enough
    assert ("DELETE", "/memories/70", None, None) in nas.calls
    assert next(m for m in posted if m["kind"] == "self")["content"] == "Lately I've been getting a bit more sarcastic than a month ago."

    result = nas.bodies("POST", "/reflections/5/finish")[0]["result"]
    assert result["written"] == {"topic": 0, "joke": 1, "opinion": 1, "question": 1, "self": 1}
    assert result["relationships"] == 2 and result["backend"] == "codex" and result["summary"] == ANSWER["summary"]
    assert {t["trait"]: (t["from"], t["to"]) for t in result["traits"]}["blunt"] == (0.89, 0.9)
    assert reflect.status()["last_result"]["id"] == 5 and reflect.status()["running"] is None


def test_caps_per_kind(nas, think):
    nas.routes[("POST", "/memories/search")] = lambda p, b: []
    think(fresh())
    reflect.poll_once()
    kinds = [m["kind"] for m in nas.bodies("POST", "/memories")]
    assert kinds.count("topic") == reflect.MAX_PER_KIND == 5 and kinds.count("joke") == 2


def test_no_self_note_under_the_threshold(nas, think):
    answer = fresh()
    answer["ohm_lines"] = []
    flat = {"traits": TRAITS["traits"], "history": [{"trait": "sarcastic", "value": 0.55, "reason": ""}]}
    nas.routes[("GET", "/traits")] = lambda p, b: flat
    think(answer)
    reflect.poll_once()
    assert not nas.bodies("PUT", "/traits/ohm")
    assert "self" not in [m["kind"] for m in nas.bodies("POST", "/memories")]
    assert not any(c[0] == "DELETE" for c in nas.calls)


def test_no_brain_answer_finishes_with_an_error(nas, think):
    think(None)
    reflect.poll_once()
    finish = nas.bodies("POST", "/reflections/5/finish")
    assert len(finish) == 1 and "No brain backend" in finish[0]["error"] and "result" not in finish[0]
    assert not nas.bodies("POST", "/memories")
    assert reflect.status()["last_result"]["error"]


def test_nothing_due_or_taken(nas, think):
    tasks = think(fresh())
    nas.routes[("GET", "/reflections/due")] = lambda p, b: None
    assert reflect.poll_once() is None
    nas.routes[("GET", "/reflections/due")] = lambda p, b: {"id": 6, "character": "ohm"}  # its claim 404s here
    assert reflect.poll_once() is None and not tasks


def test_off_without_the_nas(monkeypatch):
    monkeypatch.setattr(reflect, "MEMORY_API_URL", "")
    assert reflect.poll_once() is None
    reflect.start()
    assert reflect.status()["polling"] is False


def test_trait_math_and_notes():
    traits = [{"trait": "wholesome", "value": 0.5, "floor": 0.45, "ceiling": 1}]
    moves = reflect.trait_moves([line(["wholesome"], 0)] * 4, traits)
    assert moves == [{"trait": "wholesome", "from": 0.5, "to": 0.47, "reason": "4 wholesome lines, reception 0.00"}]
    assert reflect.trait_moves([line(["wholesome"], 0)] * 4, [{**traits[0], "floor": 0.49}])[0]["to"] == 0.49
    assert reflect.self_note([{"trait": "wholesome", "value": 0.6}, {"trait": "sarcastic", "value": 0.7}],
                             [{"trait": "wholesome", "value": 0.7}, {"trait": "sarcastic", "value": 0.5}], []) == \
        "Lately I've been getting a bit less wholesome and more sarcastic than a month ago."
    long = "; ".join(f"note {i:03d} " + "x" * 20 for i in range(20))
    merged = reflect.merge_notes(long, ["newest"])
    assert len(merged) <= reflect.NOTES_CHARS and merged.endswith("; newest") and "note 019" in merged
    assert reflect.merge_notes("a; b", ["B", " c "]) == "a; b; c"
