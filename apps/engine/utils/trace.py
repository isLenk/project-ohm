"""Per-turn stage tracing for the dashboard's Pipeline tab.
Posts each turn to the controller app (POST /api/turns), coalesced; never raises into the engine.
"""
import itertools
import threading
import time

import requests

CONTROLLER_URL = "http://localhost:3001"  # the controller app (pyhandler.interface_api); optional
POST_TIMEOUT = 2
COALESCE_S = 0.12      # changes within this go out as one post
BACKOFF_S = 15         # pause posting after the app doesn't answer
KEEP = 8               # turns kept, for late stage updates
NOTE_LIMIT = 60
LOG_LIMIT = 40         # log lines per stage
REPLY_LIMIT = 600
HEARING_STALE_MS = 30_000  # older listening turns (noise) aren't taken over

# latency.mark() name -> stage
MARK_STAGE = {
    "transcript": "stt", "bot_got_text": "turn", "pre_checks": "checks", "llm_stream_open": "llm",
    "llm_first_token": "llm", "filler": "tts", "first_tts_audio": "tts", "playback": "playback",
}

_lock = threading.RLock()
_turns: dict[str, dict] = {}
_order: list[str] = []
_current: str | None = None
_ids = itertools.count(1)
_dirty: set[str] = set()
_wake = threading.Condition(_lock)
_sender: threading.Thread | None = None
_idle = threading.Event()  # set while nothing is queued
_idle.set()
_down_until = 0.0
_post = requests.post


def now_ms() -> int:
    return int(time.time() * 1000)


def _new(kind: str, character: str) -> dict:
    return {
        "id": f"{int(time.time())}-{next(_ids)}", "kind": kind, "character": character, "speakers": [], "said": "",
        "status": "running", "started_at": now_ms(), "ended_at": None, "stages": {}, "marks": {}, "reply": "",
    }


def _keep(turn: dict) -> str:
    global _current
    _turns[turn["id"]] = turn
    _order.append(turn["id"])
    while len(_order) > KEEP:
        _turns.pop(_order.pop(0), None)
    _current = turn["id"]
    return turn["id"]


def _get(turn_id=None) -> dict | None:
    return _turns.get(turn_id or _current)


def _touch(turn_id: str):
    _dirty.add(turn_id)
    _idle.clear()
    _wake.notify_all()
    _ensure_sender()


def current_id() -> str | None:
    """The open turn's id, for callbacks that outlive it"""
    with _lock:
        return _current


def hearing(speaker: str, character: str = "") -> str | None:
    """Someone started talking: a cut-in mid-reply, else a listening turn begin() takes over"""
    try:
        with _lock:
            turn = _get()
            if turn is not None and turn["status"] == "running":
                _add_log(turn, "playback", f"{speaker} is cutting in")
                _touch(turn["id"])
                return turn["id"]
            if turn is None or turn["status"] != "hearing" or now_ms() - turn["started_at"] > HEARING_STALE_MS:
                turn = _new("voice", character)
                turn["status"] = "hearing"
                _keep(turn)
                _set_stage(turn, "discord", "running", "in the call")
                _set_stage(turn, "stt", "running", "listening")
            if speaker and speaker not in turn["speakers"]:
                turn["speakers"].append(speaker)
                _set_stage(turn, "discord", "running", ", ".join(turn["speakers"]) + " speaking")
            _touch(turn["id"])
            return turn["id"]
    except Exception:
        return None


def begin(kind: str, said_by: dict | None = None, character: str = "", note: str | None = None) -> str | None:
    """Open a turn. kind: voice | idle | text. said_by: {speaker name: what they said}"""
    try:
        with _lock:
            turn = _get()
            adopt = (kind == "voice" and turn is not None and turn["status"] == "hearing"
                     and now_ms() - turn["started_at"] <= HEARING_STALE_MS)
            if not adopt:
                turn = _new(kind, character)
                _keep(turn)
            turn["kind"] = kind
            turn["status"] = "running"
            if character:
                turn["character"] = character
            if said_by:
                turn["speakers"] = list(said_by)
                turn["said"] = "\n".join(f"{s}: {t}" for s, t in said_by.items())[:REPLY_LIMIT]
            if kind == "voice":
                who = ", ".join(turn["speakers"]) or "someone"
                _set_stage(turn, "discord", "done", f"{who} in the call")
                _set_stage(turn, "stt", "done", "transcript in")
                for s, t in (said_by or {}).items():
                    _add_log(turn, "stt", f"{s}: {t}")
                _set_stage(turn, "queue", "done", note or "1 line")
            elif kind == "idle":
                _set_stage(turn, "idle", "done", note or "quiet for a while")
            elif kind == "text":
                _set_stage(turn, "discord", "done", note or "message in")
                for s, t in (said_by or {}).items():
                    _add_log(turn, "discord", f"{s}: {t}")
            _touch(turn["id"])
            return turn["id"]
    except Exception:
        return None


def _set_stage(turn: dict, name: str, status: str, note: str | None = None):
    at = now_ms()
    stage = turn["stages"].setdefault(name, {"status": "waiting", "started_at": at, "ended_at": None, "note": "", "logs": []})
    stage["status"] = status
    if status in ("done", "skipped", "failed"):
        stage["ended_at"] = at
    elif status == "running":
        stage["started_at"] = at
        stage["ended_at"] = None
    if note is not None:
        stage["note"] = str(note)[:NOTE_LIMIT]


def _add_log(turn: dict, name: str, text: str):
    stage = turn["stages"].setdefault(name, {"status": "running", "started_at": now_ms(), "ended_at": None, "note": "", "logs": []})
    stage["logs"].append({"at": now_ms(), "text": str(text)[:400]})
    del stage["logs"][:-LOG_LIMIT]


def stage(name: str, status: str, note: str | None = None, log: str | None = None, turn: str | None = None):
    """status: running | done | skipped | failed | waiting"""
    try:
        with _lock:
            t = _get(turn)
            if t is None:
                return
            _set_stage(t, name, status, note)
            if log:
                _add_log(t, name, log)
            _touch(t["id"])
    except Exception:
        pass


def log(name: str, text: str, turn: str | None = None):
    """A line for a stage's hover"""
    try:
        with _lock:
            t = _get(turn)
            if t is None:
                return
            _add_log(t, name, text)
            _touch(t["id"])
    except Exception:
        pass


def mark(name: str, ms: float):
    """A latency mark (utils/latency.py)"""
    try:
        with _lock:
            t = _get()
            if t is None or name in t["marks"]:
                return
            t["marks"][name] = round(ms)
            if name in MARK_STAGE:
                _add_log(t, MARK_STAGE[name], f"{name} +{ms:.0f} ms")
            _touch(t["id"])
    except Exception:
        pass


def reply(text: str, turn: str | None = None):
    """The reply so far"""
    try:
        with _lock:
            t = _get(turn)
            if t is None:
                return
            t["reply"] = text[-REPLY_LIMIT:]
            _touch(t["id"])
    except Exception:
        pass


def end(status: str = "done", note: str | None = None, turn: str | None = None):
    """Close the turn: done | cut | failed. Only the first end() counts."""
    try:
        with _lock:
            t = _get(turn)
            if t is None or t["status"] not in ("running", "hearing"):
                return
            t["status"] = status
            t["ended_at"] = now_ms()
            if note:
                t["end_note"] = str(note)[:200]
            for s in t["stages"].values():
                if s["status"] == "running":
                    s["status"] = "failed" if status == "failed" else "skipped"
                    s["ended_at"] = t["ended_at"]
            _touch(t["id"])
    except Exception:
        pass


def current() -> dict | None:
    """A copy of the open turn"""
    with _lock:
        t = _get()
        return None if t is None else _snapshot(t)


def _snapshot(t: dict) -> dict:
    return {**t, "stages": {k: {**v, "logs": list(v["logs"])} for k, v in t["stages"].items()}, "marks": dict(t["marks"])}


def _ensure_sender():
    global _sender
    if _sender is None or not _sender.is_alive():
        _sender = threading.Thread(target=_send_loop, name="trace-sender", daemon=True)
        _sender.start()


def _send_loop():
    global _down_until
    while True:
        with _lock:
            while not _dirty:
                _wake.wait()
        time.sleep(COALESCE_S)
        with _lock:
            snaps = [_snapshot(_turns[i]) for i in _dirty if i in _turns]
            _dirty.clear()
        if time.time() >= _down_until:
            for snap in snaps:
                try:
                    _post(f"{CONTROLLER_URL}/api/turns", json=snap, timeout=POST_TIMEOUT)
                except Exception:
                    _down_until = time.time() + BACKOFF_S
                    break
        with _lock:
            if not _dirty:
                _idle.set()


def flush(timeout: float = 1.0) -> bool:
    """Wait until everything queued is posted"""
    return _idle.wait(timeout)


def reset():
    """Forget every turn"""
    global _current, _down_until
    with _lock:
        _turns.clear()
        _order.clear()
        _dirty.clear()
        _idle.set()
        _current = None
        _down_until = 0.0
