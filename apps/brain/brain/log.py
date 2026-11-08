"""Every call shows up in the app: a line on the console, and a log row and a timing on the NAS."""

import threading

import requests

from . import config

TIMEOUT = (2, 5)    # (connect, read) seconds; it's in the background, but don't pile up threads
PREVIEW = 200       # characters of the answer kept in the log row

_session = requests.Session()
_session.headers["X-API-Key"] = config.MEMORY_API_KEY


def report(task, backend, ms: int, ok: bool, answer: str = "", usage: dict | None = None, usd: float = 0.0):
    """Print what happened and send it to the NAS without waiting. Does nothing on the NAS side when
    MEMORY_API_URL isn't set."""
    usage = usage or {}
    if ok:
        print(f"[brain] {task.role} via {backend}: {ms} ms")
        message = f"{task.role} via {backend} in {ms} ms"
    else:
        print(f"[brain] {task.role} failed everywhere ({ms} ms)")
        message = f"{task.role} failed everywhere"
    if not config.MEMORY_API_URL:
        return
    data = {"role": task.role, "backend": backend, "ms": ms, "ok": ok,
            "prompt_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("output_tokens"),
            "cost_usd": round(usd, 6), "preview": (answer or "")[:PREVIEW]}
    if task.items:
        data["items"] = task.items
    posts = [("/logs", {"level": "info" if ok else "warning", "source": "brain", "message": message, "data": data})]
    if ok:
        posts.append(("/metrics", {"model": backend, "prompt_tokens": usage.get("prompt_tokens"),
                                   "output_tokens": usage.get("output_tokens"), "llm_ms": ms}))
    threading.Thread(target=_send, args=(posts,), daemon=True).start()


def _send(posts):
    for path, body in posts:
        try:
            _session.post(config.MEMORY_API_URL + path, json=body, timeout=TIMEOUT)
        except requests.RequestException:
            pass
