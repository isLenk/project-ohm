"""Small client for llama-server's OpenAI-compatible API (the model Ohm already runs on :5000)."""

import json
import re
from typing import Optional

import requests

from .config import LLM_SERVER_URL, LLM_URL

TIMEOUT = (3, 60)


def up() -> bool:
    try:
        return requests.get(f"{LLM_SERVER_URL}/health", timeout=2).ok
    except requests.RequestException:
        return False


def chat(system: str, user: str, max_tokens: int = 200, temperature: float = 0.1) -> Optional[str]:
    r = requests.post(
        f"{LLM_URL}/chat/completions",
        json={"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
              "max_tokens": max_tokens, "temperature": temperature},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


def chat_json(system: str, user: str, max_tokens: int = 300) -> Optional[dict | list]:
    text = chat(system, user, max_tokens=max_tokens)
    if not text:
        return None
    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
