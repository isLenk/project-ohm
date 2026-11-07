"""Starts, follows and cancels LoRA training runs. The training itself runs in worker.py as a subprocess."""

import json
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Iterator, Optional

from .. import export, gpu
from ..config import REPO_ROOT, RUNS_DIR, TRAIN_SIMULATE, TRAIN_VRAM_GB
from ..errors import ApiError, not_found
from ..project import now_ms

STAGES = ["export", "tokenize", "train", "save"]
WEIGHTS = {"export": 0.02, "tokenize": 0.03, "train": 0.85, "save": 0.10}
FINAL = {"succeeded", "failed", "cancelled"}

DEFAULT_CONFIG = {
    "method": "lora", "r": 16, "alpha": 32, "dropout": 0.05, "target_modules": "all-linear",
    "lr": 2e-4, "epochs": 3, "max_len": 1024, "batch_size": "auto", "grad_accum": "auto",
    "load_in_4bit": True, "seed": 1337, "speakers": None,
}

_procs: dict[str, subprocess.Popen] = {}


def emit(run_dir: Path, event: str, **data) -> None:
    with open(run_dir / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"event": event, "t": time.time(), **data}) + "\n")


def events(run_dir: Path) -> list[dict]:
    path = Path(run_dir) / "events.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def active(registry) -> Optional[dict]:
    return registry.one("SELECT * FROM train_runs WHERE status IN ('queued','running') ORDER BY started_at DESC LIMIT 1")


def get(registry, run_id: str) -> dict:
    row = registry.one("SELECT * FROM train_runs WHERE id = ?", (run_id,))
    if not row:
        raise not_found("Run")
    evs = events(Path(row["run_dir"]))
    progress = next((e for e in reversed(evs) if e["event"] == "progress"), None)
    losses = [{"step": e["step"], "loss": e["loss"]} for e in evs if e["event"] == "progress" and e.get("loss") is not None]
    return {**row, "config": json.loads(row["config"]), "stopped_services": json.loads(row["stopped_services"]),
            "progress": progress, "losses": losses}


def preflight(project, registry, config: dict) -> dict:
    """What the Train modal shows before starting: examples, VRAM, trainability."""
    act = registry.get("active", {}) or {}
    base = registry.one("SELECT * FROM base_models WHERE id = ?", (act.get("base_model_id"),)) if act.get("base_model_id") else None
    prev = export.preview(project, speakers=config.get("speakers"))
    status = gpu.status()
    need = TRAIN_VRAM_GB
    return {
        "base_model": base, "trainable": bool(base and base["hf_path"]), "simulate": TRAIN_SIMULATE,
        "export": prev, "gpu": status, "need_gb": need,
        "enough_vram": TRAIN_SIMULATE or bool(status and status["free_gb"] >= need),
        "active_run": active(registry),
    }


def start(project, registry, config: Optional[dict], stop_pids: list[int]) -> dict:
    config = {**DEFAULT_CONFIG, **(config or {})}
    if active(registry):
        raise ApiError(409, "run_active", "A training run is already going.")
    act = registry.get("active", {}) or {}
    if not act.get("base_model_id"):
        raise ApiError(409, "no_model", "Pick a base model first.")
    base = registry.base(act["base_model_id"])
    if not base["hf_path"] and not TRAIN_SIMULATE:
        raise ApiError(409, "base_not_trainable",
                       "This model has no Hugging Face weights to train on. Set them in the model menu.")
    status = gpu.status()
    if not TRAIN_SIMULATE:
        if status is None:
            raise ApiError(409, "no_gpu", "No NVIDIA GPU found (nvidia-smi isn't available).")
        freed = sum(p["used_gb"] for p in status["processes"] if p["pid"] in stop_pids)
        if status["free_gb"] + freed < TRAIN_VRAM_GB:
            raise ApiError(409, "not_enough_vram", "Not enough free VRAM to train.",
                           free_gb=status["free_gb"], need_gb=TRAIN_VRAM_GB, processes=status["processes"])

    data = export.build(project, speakers=config.get("speakers"))
    if not data["examples"]:
        raise ApiError(422, "no_examples", "Approve at least one conversation with replies first.")

    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True)
    with open(run_dir / "dataset.jsonl", "w", encoding="utf-8") as f:
        for e in data["examples"]:
            f.write(json.dumps({**e, "messages": export.chat_messages(e)}, ensure_ascii=False) + "\n")
    (run_dir / "config.json").write_text(json.dumps({
        **config, "hf_path": base["hf_path"], "base_model_id": base["id"], "run_id": run_id,
        "examples": len(data["examples"]),
    }, indent=2))
    emit(run_dir, "stage", stage="export", stage_index=0, stages=len(STAGES), elapsed_s=0)
    emit(run_dir, "progress", stage="export", stage_index=0, stages=len(STAGES), overall=WEIGHTS["export"],
         elapsed_s=0, eta_s=None, examples=len(data["examples"]))

    stopped = gpu.stop(stop_pids) if stop_pids else []
    registry.run(
        "INSERT INTO train_runs (id, project_path, base_model_id, status, stage, config, dataset_sha, example_count, "
        "started_at, run_dir, stopped_services) VALUES (?, ?, ?, 'running', 'tokenize', ?, ?, ?, ?, ?, ?)",
        (run_id, str(project.path), base["id"], json.dumps(config), data["sha256"], len(data["examples"]),
         now_ms(), str(run_dir), json.dumps(stopped)),
    )
    with project.tx() as conn:
        conn.execute("INSERT OR REPLACE INTO project_runs (run_id, dataset_sha) VALUES (?, ?)", (run_id, data["sha256"]))

    cmd = [sys.executable, "-m", "curator_service.app.train.worker", "--run-dir", str(run_dir)]
    if TRAIN_SIMULATE:
        cmd.append("--simulate")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    log = open(run_dir / "worker.log", "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=REPO_ROOT / "apps" / "curator", stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    _procs[run_id] = proc
    registry.run("UPDATE train_runs SET pid = ? WHERE id = ?", (proc.pid, run_id))
    threading.Thread(target=_monitor, args=(registry, run_id, proc), daemon=True).start()
    return get(registry, run_id)


def _finish(registry, run_id: str) -> None:
    row = registry.one("SELECT * FROM train_runs WHERE id = ?", (run_id,))
    run_dir = Path(row["run_dir"])
    evs = events(run_dir)
    last = next((e for e in reversed(evs) if e["event"] in ("done", "error", "cancelled")), None)
    if row["status"] == "cancelled" or (last and last["event"] == "cancelled"):
        status, error = "cancelled", None
    elif last and last["event"] == "done":
        status, error = "succeeded", None
    else:
        status = "failed"
        error = (last or {}).get("error") or _log_tail(run_dir) or "The training process stopped unexpectedly."
        if not last or last["event"] != "error":
            emit(run_dir, "error", status="failed", error=error, log_tail=_log_tail(run_dir))

    adapter_id = None
    if status == "succeeded":
        try:
            adapter = registry.add_adapter(last["adapter_gguf"], row["base_model_id"],
                                           name=f"LoRA {time.strftime('%b %d %H:%M')}",
                                           peft_path=last.get("peft_dir"), run_id=run_id)
            adapter_id = adapter["id"]
            registry.put("active", {"base_model_id": row["base_model_id"], "adapter_id": adapter_id})
        except ApiError as e:
            status, error = "failed", f"Training finished but the adapter could not be registered: {e.message}"
    registry.run(
        "UPDATE train_runs SET status = ?, finished_at = ?, final_loss = ?, error = ?, adapter_id = ?, pid = NULL WHERE id = ?",
        (status, now_ms(), (last or {}).get("final_loss"), error, adapter_id, run_id),
    )
    _restart_services(registry, row, adapter_id)


def _restart_services(registry, row: dict, adapter_id: Optional[str]) -> None:
    stopped = json.loads(row["stopped_services"])
    llama = next((s for s in stopped if s.get("is_llama")), None)
    if not llama:
        return
    base = registry.base(row["base_model_id"])
    adapters = [a["gguf_path"] for a in registry.all("SELECT gguf_path FROM adapters WHERE base_model_id = ?", (base["id"],))]
    pid = gpu.start(llama, gpu.llama_command(llama["cmdline"], base["gguf_path"], adapters))
    if pid and gpu.wait_for_llama() and adapter_id:
        gpu.apply_adapter(registry.adapter(adapter_id)["gguf_path"])


def _log_tail(run_dir: Path, lines: int = 20) -> str:
    p = run_dir / "worker.log"
    if not p.exists():
        return ""
    return "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def _monitor(registry, run_id: str, proc: Optional[subprocess.Popen], pid: Optional[int] = None) -> None:
    if proc is not None:
        proc.wait()
    else:
        while gpu.psutil and gpu.psutil.pid_exists(pid):
            time.sleep(2)
    _procs.pop(run_id, None)
    _finish(registry, run_id)


def resume(registry) -> None:
    """After a service restart: follow runs whose worker is still alive, fail the rest."""
    for row in registry.all("SELECT * FROM train_runs WHERE status IN ('queued','running')"):
        if row["pid"] and gpu.psutil and gpu.psutil.pid_exists(row["pid"]):
            threading.Thread(target=_monitor, args=(registry, row["id"], None, row["pid"]), daemon=True).start()
        else:
            _finish(registry, row["id"])


def cancel(registry, run_id: str) -> dict:
    row = registry.one("SELECT * FROM train_runs WHERE id = ?", (run_id,))
    if not row:
        raise not_found("Run")
    if row["status"] in FINAL:
        return get(registry, run_id)
    registry.run("UPDATE train_runs SET status = 'cancelled' WHERE id = ?", (run_id,))
    emit(Path(row["run_dir"]), "cancelled", status="cancelled")
    proc = _procs.get(run_id)
    pid = proc.pid if proc else row["pid"]
    if pid and gpu.psutil:
        try:
            p = gpu.psutil.Process(pid)
            children = p.children(recursive=True)
            for c in [p, *children]:
                c.terminate()
            _, alive = gpu.psutil.wait_procs([p, *children], timeout=10)
            for c in alive:
                c.kill()
        except gpu.psutil.NoSuchProcess:
            pass
    elif proc:
        proc.terminate()
    return get(registry, run_id)


def stream(registry, run_id: str) -> Iterator[str]:
    """SSE: everything so far, then new events until the run ends."""
    row = registry.one("SELECT * FROM train_runs WHERE id = ?", (run_id,))
    if not row:
        raise not_found("Run")
    path = Path(row["run_dir"]) / "events.jsonl"
    sent = 0
    idle = 0
    while True:
        evs = events(path.parent)
        for e in evs[sent:]:
            yield f"event: {e['event']}\ndata: {json.dumps(e)}\n\n"
        sent = len(evs)
        status = registry.one("SELECT status FROM train_runs WHERE id = ?", (run_id,))["status"]
        if status in FINAL and any(e["event"] in ("done", "error", "cancelled") for e in evs):
            final = get(registry, run_id)
            yield f"event: end\ndata: {json.dumps({'status': final['status'], 'adapter_id': final['adapter_id'], 'error': final['error']})}\n\n"
            return
        idle += 1
        if idle % 15 == 0:
            yield ": keep-alive\n\n"
        time.sleep(1)
