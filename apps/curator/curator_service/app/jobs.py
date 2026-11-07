"""Background jobs (embeddings, memory extraction, downloads) with progress the UI can follow over SSE."""

import threading
import time
import traceback
import uuid
from typing import Callable, Optional

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


class Job:
    def __init__(self, job_id: str):
        self.id = job_id

    def progress(self, done: int, total: int, note: Optional[str] = None) -> None:
        with _lock:
            _jobs[self.id].update(done=done, total=total, note=note, updated=time.time())

    @property
    def cancelled(self) -> bool:
        return _jobs[self.id].get("cancel", False)


def start(kind: str, fn: Callable[[Job], Optional[dict]], dedupe: bool = True) -> dict:
    """Run fn(job) in a thread. With dedupe, a second start of the same kind returns the running job."""
    with _lock:
        if dedupe:
            for j in _jobs.values():
                if j["kind"] == kind and j["status"] == "running":
                    return dict(j)
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"id": job_id, "kind": kind, "status": "running", "done": 0, "total": 0,
                         "note": None, "error": None, "result": None, "updated": time.time()}

    def runner():
        job = Job(job_id)
        try:
            result = fn(job)
            status = "cancelled" if job.cancelled else "done"
            with _lock:
                _jobs[job_id].update(status=status, result=result, updated=time.time())
        except Exception as e:  # report, don't crash the server
            traceback.print_exc()
            with _lock:
                _jobs[job_id].update(status="failed", error=str(e), updated=time.time())

    threading.Thread(target=runner, daemon=True, name=f"job-{kind}").start()
    return get(job_id)


def get(job_id: str) -> Optional[dict]:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def listing() -> list[dict]:
    with _lock:
        return [dict(j) for j in _jobs.values()]


def cancel(job_id: str) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["cancel"] = True
