"""VRAM checks, and stopping/starting llama-server around training.

Only processes the user confirmed in the Train modal are stopped. llama-server is
restarted afterwards with the same command line plus every adapter for its model.
Other stopped processes (TTS, the bot) are reported so the user starts them again.
"""

import shutil
import subprocess
import sys
import time
from typing import Optional

import requests

from .config import LLAMA_CPP_DIR, LLM_SERVER_URL

try:
    import psutil
except ImportError:  # psutil is in requirements; this keeps tests importable without it
    psutil = None


def status() -> Optional[dict]:
    """GPU name, memory and the processes using it, from nvidia-smi. None when there is no NVIDIA GPU."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        gpu = subprocess.run(
            [smi, "--query-gpu=name,memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip().splitlines()[0]
        apps = subprocess.run(
            [smi, "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()
    except (subprocess.SubprocessError, IndexError, OSError):
        return None
    name, total, used, free = [x.strip() for x in gpu.split(",")]
    processes = []
    for line in apps:
        parts = [x.strip() for x in line.split(",")]
        if len(parts) == 3 and parts[0].isdigit():
            pid = int(parts[0])
            processes.append({"pid": pid, "name": _label(pid, parts[1]), "used_gb": round(_mib(parts[2]) / 1024, 2)})
    return {"name": name, "total_gb": round(_mib(total) / 1024, 2), "used_gb": round(_mib(used) / 1024, 2),
            "free_gb": round(_mib(free) / 1024, 2), "processes": processes}


def _mib(v: str) -> float:
    try:
        return float(v)
    except ValueError:
        return 0.0


def _label(pid: int, fallback: str) -> str:
    if psutil is None:
        return fallback
    try:
        p = psutil.Process(pid)
        cmd = " ".join(p.cmdline()[:3])
        if "llama-server" in p.name().lower():
            return "llama-server (Ohm's LLM)"
        if "tts" in cmd.lower():
            return f"TTS ({p.name()})"
        if "main.py" in cmd:
            return f"Ohm bot ({p.name()})"
        return p.name()
    except Exception:
        return fallback


def snapshot(pid: int) -> Optional[dict]:
    if psutil is None:
        return None
    try:
        p = psutil.Process(pid)
        return {"pid": pid, "name": p.name(), "cmdline": p.cmdline(), "cwd": p.cwd(),
                "is_llama": "llama-server" in p.name().lower()}
    except Exception:
        return None


def stop(pids: list[int]) -> list[dict]:
    """Stop the given processes, remembering how to start llama-server again."""
    stopped = []
    for pid in pids:
        snap = snapshot(pid)
        if not snap:
            continue
        try:
            p = psutil.Process(pid)
            p.terminate()
            try:
                p.wait(10)
            except psutil.TimeoutExpired:
                p.kill()
            stopped.append(snap)
        except Exception as e:
            print(f"[curator] could not stop {pid}: {e}")
    time.sleep(2)  # let the driver release memory
    return stopped


def llama_command(cmdline: list[str], model_path: Optional[str], adapters: list[str]) -> list[str]:
    """Same command, with -m pointed at `model_path` and the adapters loaded at scale 0."""
    out, skip = [], False
    for i, arg in enumerate(cmdline):
        if skip:
            skip = False
            continue
        if arg in ("--lora", "--lora-scaled"):
            skip = True
            continue
        if arg == "--lora-init-without-apply":
            continue
        if arg in ("-m", "--model") and model_path and i + 1 < len(cmdline):
            out += [arg, model_path]
            skip = True
            continue
        out.append(arg)
    if adapters:
        out += ["--lora", ",".join(adapters), "--lora-init-without-apply"]
    return out


def default_llama(model_path: str, adapters: list[str]) -> dict:
    """What tools/_run_llama.cmd runs, for when llama-server has never been seen running.

    The vision projector (--mmproj) isn't added: there's no way to tell which one goes with a model.
    Start llama-server once by hand with it and that command line is remembered instead.
    """
    exe = LLAMA_CPP_DIR / "bin" / ("llama-server.exe" if sys.platform == "win32" else "llama-server")
    cmd = [str(exe), "-m", model_path, "--jinja", "--reasoning", "off", "-ngl", "99", "-c", "32768",
           "--port", LLM_SERVER_URL.rsplit(":", 1)[-1]]
    return {"name": "llama-server", "cwd": str(LLAMA_CPP_DIR), "cmdline": llama_command(cmd, model_path, adapters)}


def uptime(pid: int) -> Optional[float]:
    """Seconds the process has been running."""
    if psutil is None:
        return None
    try:
        return max(0.0, time.time() - psutil.Process(pid).create_time())
    except Exception:
        return None


def start(snap: dict, cmdline: list[str]) -> Optional[int]:
    flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    try:
        p = subprocess.Popen(cmdline, cwd=snap.get("cwd") or None, creationflags=flags)
        return p.pid
    except OSError as e:
        print(f"[curator] could not restart {snap['name']}: {e}")
        return None


def running_llama() -> Optional[dict]:
    if psutil is None:
        return None
    for p in psutil.process_iter(["pid", "name"]):
        if "llama-server" in (p.info["name"] or "").lower():
            return snapshot(p.info["pid"])
    return None


def wait_for_llama(timeout: float = 120) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if requests.get(f"{LLM_SERVER_URL}/health", timeout=2).ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


def loaded_adapters() -> Optional[list[dict]]:
    try:
        r = requests.get(f"{LLM_SERVER_URL}/lora-adapters", timeout=3)
        return r.json() if r.ok else None
    except requests.RequestException:
        return None


def apply_adapter(path: Optional[str]) -> bool:
    """Turn on one loaded adapter (scale 1) and the rest off. False if it isn't loaded."""
    loaded = loaded_adapters()
    if loaded is None:
        return False
    def same(a, b) -> bool:
        return a and b and a.replace("\\", "/").lower() == b.replace("\\", "/").lower()
    if path and not any(same(a.get("path"), path) for a in loaded):
        return False
    scales = [{"id": a["id"], "scale": 1.0 if same(a.get("path"), path) else 0.0} for a in loaded]
    try:
        return requests.post(f"{LLM_SERVER_URL}/lora-adapters", json=scales, timeout=5).ok
    except requests.RequestException:
        return False
