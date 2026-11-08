"""Which backends each role uses, read from env vars (the Electron app writes them to .env or the child env).

    BRAIN_ROLE_JUDGE=codex,qwen     try Codex first, then the local judge model
    BRAIN_LOCAL_ONLY=1              nothing leaves the PC
"""

import json
import os
import sys
import threading
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]  # apps/brain/brain/config.py
load_dotenv(REPO_ROOT / ".env")

# Backends tried in order for each role, unless BRAIN_ROLE_<ROLE> says otherwise
DEFAULT_ROLES = {
    "classify": "local",          # search_query, about_screen: tiny and needs to be fast
    "extract": "local",           # memory notes from chat lines
    "brief": "codex,local",
    "curate": "codex,local",
    "judge": "codex,qwen",        # qwen needs Ohm's llama-server stopped, so it's the fallback
    "reflect": "codex,deepseek",
    "label": "codex",
    "screen": "codex",            # the engine's opt-in screen description; no local fallback, live replies use it
}
LOCAL = ("local", "qwen")          # everything else sends the prompt off the PC
PAID = ("deepseek", "openrouter")  # billed per token, so they count against BRAIN_DAILY_USD

# Rough USD per million tokens (prompt, output), only used for the daily cap
PRICES = {"deepseek": (0.27, 1.10), "openrouter": (0.50, 1.50)}

LLM_URL = os.getenv("CURATOR_LLM_URL", "http://127.0.0.1:5000/v1").rstrip("/")
JUDGE_PORT = int(os.getenv("CURATOR_JUDGE_PORT", "5055"))
LLAMA_CPP_DIR = Path(os.getenv("LLAMA_CPP_DIR", REPO_ROOT.parent / "llama.cpp"))  # same defaults as the Curator
JUDGE_MODEL = Path(os.getenv("CURATOR_JUDGE_MODEL",
                             Path(os.getenv("CURATOR_MODELS_DIR", LLAMA_CPP_DIR / "models")) / "Qwen3.6-27B-Q4_K_M.gguf"))

MEMORY_API_URL = (os.getenv("MEMORY_API_URL") or "").rstrip("/")
MEMORY_API_KEY = os.getenv("MEMORY_API_KEY", "")

_backends = {}
_spend_lock = threading.Lock()


def chain(role: str) -> list:
    """The backend names `role` tries, in order. Read every time, so a changed env shows up without a restart.
    A new role only needs BRAIN_ROLE_<ROLE> set (or a line in DEFAULT_ROLES)."""
    names = os.getenv(f"BRAIN_ROLE_{role.upper()}") or DEFAULT_ROLES.get(role, "")
    return [n.strip().lower() for n in names.split(",") if n.strip()]


def roles() -> dict:
    """Role -> list of backend names, for every known role."""
    return {role: chain(role) for role in DEFAULT_ROLES}


def local_only() -> bool:
    return os.getenv("BRAIN_LOCAL_ONLY") == "1"


def is_cloud(name: str) -> bool:
    return name not in LOCAL


def backend(name: str):
    """The backend called `name`, made once and kept, or None for a name that isn't one of ours."""
    if name not in _backends:
        from .backends import CliBackend, OpenAIBackend
        made = {
            "local": lambda: OpenAIBackend("local", LLM_URL, lora_off=True, timeout=(2, 30)),
            "qwen": lambda: OpenAIBackend("qwen", f"http://127.0.0.1:{JUDGE_PORT}/v1", timeout=(5, 180)),
            "deepseek": lambda: OpenAIBackend("deepseek", "https://api.deepseek.com/v1",
                                              model=os.getenv("BRAIN_DEEPSEEK_MODEL", "deepseek-chat"),
                                              key_env="DEEPSEEK_API_KEY"),
            "openrouter": lambda: OpenAIBackend("openrouter", "https://openrouter.ai/api/v1",
                                                model=os.getenv("BRAIN_OPENROUTER_MODEL"),
                                                key_env="OPENROUTER_API_KEY"),
            "codex": lambda: CliBackend("codex", model=os.getenv("BRAIN_CODEX_MODEL"),
                                        reasoning=os.getenv("BRAIN_CODEX_REASONING", "medium")),
            "claude": lambda: CliBackend("claude", model=os.getenv("BRAIN_CLAUDE_MODEL", "haiku")),
        }.get(name)
        if not made:
            return None
        _backends[name] = made()
    return _backends[name]


# ── Daily spend on paid APIs ────────────────────────────────────────────────
def daily_usd() -> float:
    try:
        return float(os.getenv("BRAIN_DAILY_USD", "0.50"))
    except ValueError:
        return 0.50


def data_dir() -> Path:
    """The Curator's data folder (same rule as curator config._app_data)."""
    if os.getenv("CURATOR_DATA_DIR"):
        return Path(os.environ["CURATOR_DATA_DIR"])
    if sys.platform == "win32":
        return Path(os.getenv("APPDATA", Path.home())) / "ohm-curator"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ohm-curator"
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ohm-curator"


def _spend_file() -> Path:
    return data_dir() / "brain-spend.json"


def spent_today() -> float:
    try:
        saved = json.loads(_spend_file().read_text(encoding="utf-8"))
        return float(saved["usd"]) if saved.get("date") == date.today().isoformat() else 0.0
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0


def cost(name: str, usage: dict) -> float:
    """What a call cost, from its token counts. 0 for anything that isn't billed per token."""
    if name not in PRICES:
        return 0.0
    per_in, per_out = PRICES[name]
    return ((usage.get("prompt_tokens") or 0) * per_in + (usage.get("output_tokens") or 0) * per_out) / 1e6


def add_spend(usd: float):
    if usd <= 0:
        return
    with _spend_lock:
        try:
            path = _spend_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"date": date.today().isoformat(), "usd": spent_today() + usd}), encoding="utf-8")
        except OSError as e:
            print(f"[brain] could not save today's spend: {e}")


def over_cap() -> bool:
    return spent_today() >= daily_usd()
