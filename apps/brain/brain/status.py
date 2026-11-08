"""What's installed, signed in and configured, for the app's Settings > Integrations tab. Quick and never raises."""

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

import requests

from . import config

TIMEOUT = 8     # seconds per CLI check; they all run at once, so status() stays under 10 s


def _run(args):
    try:
        return subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace", timeout=TIMEOUT,
                              stdin=subprocess.DEVNULL, shell=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _cli(name: str, login: list) -> dict:
    path = shutil.which(name)
    if not path:
        return {"installed": False, "version": None, "signed_in": None, "path": None}
    v = _run([path, "--version"])
    s = _run([path, *login])
    signed_in = None
    if s is not None:
        signed_in = s.returncode == 0
        try:  # `claude auth status` says it in JSON
            signed_in = bool(json.loads(s.stdout).get("loggedIn", signed_in))
        except (ValueError, AttributeError):
            pass
    version = v.stdout.strip().splitlines()[0] if v and v.returncode == 0 and v.stdout.strip() else None
    return {"installed": True, "version": version, "signed_in": signed_in, "path": path}


def _local_up() -> bool:
    server = config.LLM_URL[: -len("/v1")] if config.LLM_URL.endswith("/v1") else config.LLM_URL
    try:
        return requests.get(f"{server}/health", timeout=2).ok
    except requests.RequestException:
        return False


def status() -> dict:
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            codex = pool.submit(_cli, "codex", ["login", "status"])
            claude = pool.submit(_cli, "claude", ["auth", "status"])
            up = pool.submit(_local_up)
            return {
                "codex": codex.result(),
                "claude": claude.result(),
                "deepseek": {"key": bool(os.getenv("DEEPSEEK_API_KEY")),
                             "model": os.getenv("BRAIN_DEEPSEEK_MODEL", "deepseek-chat")},
                "openrouter": {"key": bool(os.getenv("OPENROUTER_API_KEY")), "model": os.getenv("BRAIN_OPENROUTER_MODEL")},
                "local": {"up": up.result(), "url": config.LLM_URL},
                "qwen": {"model_present": config.JUDGE_MODEL.is_file()},
                "roles": config.roles(),
                "local_only": config.local_only(),
                "spent_today_usd": round(config.spent_today(), 4),
            }
    except Exception as e:
        print(f"[brain] status check failed: {e}")
        return {"error": str(e)}
