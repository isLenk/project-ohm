"""Paths and endpoints. Everything can be overridden through the root .env."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[4]  # apps/curator/curator_service/app/config.py
load_dotenv(REPO_ROOT / ".env")


def _app_data() -> Path:
    if os.getenv("CURATOR_DATA_DIR"):
        return Path(os.environ["CURATOR_DATA_DIR"])
    if sys.platform == "win32":
        return Path(os.getenv("APPDATA", Path.home())) / "ohm-curator"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ohm-curator"
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ohm-curator"


DATA_DIR = _app_data()
REGISTRY_PATH = DATA_DIR / "registry.sqlite"
ADAPTERS_DIR = DATA_DIR / "adapters"
RUNS_DIR = DATA_DIR / "runs"

# tools/_run_llama.cmd does `cd ..\..\llama.cpp`, so llama.cpp sits next to the repo.
LLAMA_CPP_DIR = Path(os.getenv("LLAMA_CPP_DIR", REPO_ROOT.parent / "llama.cpp"))
MODELS_DIR = Path(os.getenv("CURATOR_MODELS_DIR", LLAMA_CPP_DIR / "models"))

# The bigger model that scores an adapter's replies (train/evaluate.py). It runs on its own port while
# Ohm's llama-server is stopped, since both don't fit in 24 GB.
JUDGE_MODEL = Path(os.getenv("CURATOR_JUDGE_MODEL", MODELS_DIR / "Qwen3.6-27B-Q4_K_M.gguf"))
JUDGE_PORT = int(os.getenv("CURATOR_JUDGE_PORT", "5055"))

LLM_URL = os.getenv("CURATOR_LLM_URL", "http://127.0.0.1:5000/v1").rstrip("/")
LLM_SERVER_URL = LLM_URL[: -len("/v1")] if LLM_URL.endswith("/v1") else LLM_URL

MEMORY_API_URL = (os.getenv("MEMORY_API_URL") or "").rstrip("/")
MEMORY_API_KEY = os.getenv("MEMORY_API_KEY", "")

PORT = int(os.getenv("CURATOR_PORT", "8200"))

# Set CURATOR_TRAIN_SIMULATE=1 to fake a training run (for working on the UI without a GPU).
TRAIN_SIMULATE = os.getenv("CURATOR_TRAIN_SIMULATE") == "1"

# VRAM a QLoRA run needs. Unset: estimated from the model size (runs.need_vram)
TRAIN_VRAM_GB = float(os.environ["CURATOR_TRAIN_VRAM_GB"]) if os.getenv("CURATOR_TRAIN_VRAM_GB") else None

# Training runs in its own venv, because Unsloth's torch/numpy/transformers break the main one.
# Uses CURATOR_TRAIN_PYTHON, then <data dir>/train-venv if it exists, then this interpreter.
def _train_python() -> str:
    if os.getenv("CURATOR_TRAIN_PYTHON"):
        return os.environ["CURATOR_TRAIN_PYTHON"]
    venv = DATA_DIR / "train-venv"
    exe = venv / "Scripts" / "python.exe" if sys.platform == "win32" else venv / "bin" / "python"
    return str(exe) if exe.exists() else sys.executable


TRAIN_PYTHON = _train_python()

for d in (DATA_DIR, ADAPTERS_DIR, RUNS_DIR):
    d.mkdir(parents=True, exist_ok=True)
