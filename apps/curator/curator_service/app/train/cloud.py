"""Trains on a cloud GPU (Modal), run by worker.py --cloud. Sends the masked cloud_dataset.jsonl and
hyperparameters; gets back progress events and the PEFT adapter, converted to GGUF here."""

import io
import os
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import modal

GPU = os.getenv("CURATOR_CLOUD_GPU", "L40S")
TIMEOUT_MIN = int(os.getenv("CURATOR_CLOUD_TIMEOUT_MIN", "60"))
# Rough on-demand $/hour (modal.com/pricing)
PRICE_HR = {"T4": 0.59, "L4": 0.80, "A10G": 1.10, "L40S": 1.95, "A100-40GB": 2.10, "A100-80GB": 2.50, "H100": 3.95}
CHUNK = 16 * 1024 * 1024
# Config sent up; not speakers, it holds author ids
SENT = ("method", "r", "alpha", "dropout", "target_modules", "lr", "epochs", "max_len", "batch_size", "grad_accum",
        "load_in_4bit", "seed", "hf_path", "examples")

WORKER = Path(__file__).with_name("worker.py")

# Same versions as the local training venv
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("torch==2.14.1", "unsloth==2026.11.3", "unsloth_zoo==2026.11.3", "trl==1.15.0", "peft==0.21.2",
                 "transformers==5.17.0", "datasets==4.8.5", "bitsandbytes==0.50.2", "accelerate==1.15.0")
    .env({"HF_HOME": "/cache/hf", "HF_HUB_ENABLE_HF_TRANSFER": "0"})
    .add_local_file(str(WORKER), "/root/ohm_worker.py")
)
# Base weights cached between runs
weights = modal.Volume.from_name("ohm-curator-hf-cache", create_if_missing=True)
app = modal.App("ohm-curator-train")


@app.function(image=image, gpu=GPU, timeout=TIMEOUT_MIN * 60, volumes={"/cache": weights}, scaledown_window=2,
              max_containers=1)
def train_remote(cfg: dict, dataset: str):
    """Yields ("event", {...}) as it trains, then ("peft", bytes) chunks of the adapter .tar.gz and ("result", {...}),
    or ("error", message)"""
    import queue
    import traceback

    sys.path.insert(0, "/root")
    import ohm_worker

    started = time.time()
    run_dir = Path("/tmp/run")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "dataset.jsonl").write_text(dataset, encoding="utf-8")
    events = queue.Queue()

    class Reporter(ohm_worker.Reporter):
        def emit(self, event, **data):
            events.put(("event", {"event": event, **data}))

    result = {}

    def work():
        try:
            peft_dir, cb = ohm_worker.fit(run_dir, Reporter(run_dir), cfg)
            result.update(peft_dir=peft_dir, loss=cb.loss, eval_loss=cb.eval_loss, best_eval_loss=cb.best_eval_loss)
        except BaseException as e:
            traceback.print_exc()
            result["error"] = f"{type(e).__name__}: {e}"[:2000]
        finally:
            events.put(None)

    yield ("cloud", {"phase": "gpu_up"})  # billing starts about here
    threading.Thread(target=work, daemon=True).start()
    while (item := events.get()) is not None:
        yield item
    weights.commit()
    if "error" in result or not result.get("peft_dir"):
        yield ("error", result.get("error", "training stopped without an adapter"))
        return
    yield ("cloud", {"phase": "sending_adapter"})
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(result["peft_dir"], arcname="peft")
    data = buf.getvalue()
    for i in range(0, len(data), CHUNK):
        yield ("peft", data[i:i + CHUNK])
    yield ("result", {k: result[k] for k in ("loss", "eval_loss", "best_eval_loss")} | {"gpu_s": time.time() - started})


def _stop_on_cancel(run_dir: Path, app_id_of, done: threading.Event, cancelled):
    """Stop the Modal app on cancel, even mid-download when no events come"""
    while not done.wait(1):
        if cancelled(run_dir):
            app_id = app_id_of()
            if app_id:
                subprocess.run([sys.executable, "-m", "modal", "app", "stop", app_id], capture_output=True, timeout=60)
            return


def app_state(app_id: str):
    """The app's state from `modal app list`, or None"""
    try:
        out = subprocess.run([sys.executable, "-m", "modal", "app", "list", "--json"], capture_output=True, text=True,
                             timeout=60)
        import json
        for row in json.loads(out.stdout or "[]"):
            if app_id in [str(v) for v in row.values()]:
                return next((str(v).lower() for k, v in row.items() if "state" in k.lower()), None)
    except Exception:
        pass
    return None


def confirm_stopped(rep, app_id) -> None:
    """Report whether Modal really stopped the app"""
    if not app_id:
        return
    state = None
    for _ in range(6):  # stopping takes a moment
        state = app_state(app_id)
        if state and "stop" in state:
            break
        time.sleep(5)
    stopped = bool(state and "stop" in state)
    rep.emit("cloud", phase="stopped" if stopped else "check", state=state, at=time.time())


def run(run_dir: Path, rep, cfg: dict) -> None:
    from . import worker

    dataset = (run_dir / "cloud_dataset.jsonl").read_text(encoding="utf-8")
    sent = {k: cfg[k] for k in SENT if k in cfg}
    rep.emit("note", message=f"Training on a cloud {GPU} (Modal). Names and contact details were masked before upload. "
                             "The first run builds the environment and downloads the base model, so it starts slower.")
    rep.emit("cloud", phase="building", gpu=GPU, price_hr=PRICE_HR.get(GPU), at=time.time())
    peft, result, error, app_id = io.BytesIO(), None, None, None
    done = threading.Event()
    try:
        with modal.enable_output():
            with app.run():
                app_id = app.app_id
                try:
                    url = app.get_dashboard_url()
                except Exception:
                    url = "https://modal.com/apps"
                rep.emit("cloud", phase="waiting_gpu", app_id=app_id, url=url, at=time.time())
                threading.Thread(target=_stop_on_cancel, args=(run_dir, lambda: app.app_id, done, worker._cancelled),
                                 daemon=True).start()
                try:
                    for kind, payload in train_remote.remote_gen(sent, dataset):
                        if worker._cancelled(run_dir):
                            return  # leaving app.run() stops the GPU
                        if kind == "event":
                            event = payload.pop("event")
                            payload.pop("t", None)
                            rep.emit(event, **payload)
                        elif kind == "cloud":
                            rep.emit("cloud", **payload, at=time.time())
                        elif kind == "peft":
                            peft.write(payload)
                        elif kind == "result":
                            result = payload
                        elif kind == "error":
                            error = payload
                finally:
                    done.set()
                    rep.emit("cloud", phase="stopping", at=time.time())
    finally:
        confirm_stopped(rep, app_id)
    if worker._cancelled(run_dir):
        return
    if error or result is None:
        raise RuntimeError(f"Cloud training failed: {error or 'it ended without an adapter'}")

    peft.seek(0)
    with tarfile.open(fileobj=peft, mode="r:gz") as tar:
        tar.extractall(run_dir, filter="data")  # refuses absolute paths and ../
    usd = round(result["gpu_s"] / 3600 * PRICE_HR.get(GPU, 0), 2)
    rep.emit("note", message=f"Cloud GPU time {result['gpu_s'] / 60:.1f} min on {GPU}, about ${usd:.2f}.")
    worker.finish(run_dir, rep, cfg, run_dir / "peft", result["loss"], result["eval_loss"], result["best_eval_loss"],
                  cloud={"gpu": GPU, "gpu_s": round(result["gpu_s"]), "usd_estimate": usd})
