"""Every call shows up in the app: a line on the console, and a log row and a timing on the NAS."""

import threading

import requests

from . import config

TIMEOUT = (2, 5)    # (connect, read) seconds; it's in the background, but don't pile up threads
PREVIEW = 200       # characters of the answer kept in the log row
REASONS = 400       # characters of a failure's message

_session = requests.Session()
_session.headers["X-API-Key"] = config.MEMORY_API_KEY


def report(task, backend, ms: int, ok: bool, answer: str = "", usage: dict | None = None, usd: float = 0.0,
           errors: list | None = None):
    """Print what happened and send it to the NAS without waiting. Does nothing on the NAS side when
    MEMORY_API_URL isn't set. `errors`: why each backend failed."""
    usage = usage or {}
    if ok:
        print(f"[brain] {task.role} via {backend}: {ms} ms")
        message = f"{task.role} via {backend} in {ms} ms"
    else:
        why = "; ".join(str(e) for e in errors or []) or "every backend was skipped"
        print(f"[brain] {task.role} failed everywhere ({ms} ms): {why}")
        message = f"{task.role} failed everywhere: {why}"[:REASONS]
    if not config.MEMORY_API_URL:
        return
    data = {"role": task.role, "backend": backend, "ms": ms, "ok": ok,
            "prompt_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("output_tokens"),
            "cost_usd": round(usd, 6), "preview": (answer or "")[:PREVIEW]}
    if errors:
        data["errors"] = [str(e)[:PREVIEW] for e in errors]
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
