"""Small client for llama-server's OpenAI-compatible API (the model Ohm already runs on :5000)."""

import json
import re
import time
from typing import Optional

import requests

from .config import LLM_SERVER_URL, LLM_URL

TIMEOUT = (3, 60)
ADAPTERS_TTL_S = 15

_adapters: tuple[float, Optional[list]] = (0.0, None)


def up() -> bool:
    try:
        return requests.get(f"{LLM_SERVER_URL}/health", timeout=2).ok
    except requests.RequestException:
        return False


def _adapters_off() -> Optional[list[dict]]:
    """Every loaded adapter at scale 0, for one request. The Curator's own questions (memories, reply arrows,
    unfamiliar words) are for the base model, whichever character's adapter the server has switched on.
    None when llama-server can't list them, so the request goes out without a lora field."""
    global _adapters
    at, ids = _adapters
    if time.monotonic() - at > ADAPTERS_TTL_S:
        try:
            r = requests.get(f"{LLM_SERVER_URL}/lora-adapters", timeout=2)
            ids = [a["id"] for a in r.json()] if r.ok else None
        except (requests.RequestException, ValueError, KeyError, TypeError):
            ids = None
        _adapters = (time.monotonic(), ids)
    return [{"id": i, "scale": 0.0} for i in ids] if ids else None


def chat(system: str, user: str, max_tokens: int = 200, temperature: float = 0.1) -> Optional[str]:
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": temperature}
    off = _adapters_off()
    if off:
        body["lora"] = off
    r = requests.post(f"{LLM_URL}/chat/completions", json=body, timeout=TIMEOUT)
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


def pieces(words: list[str]) -> Optional[list[list[str]]]:
    """How the model splits each word (with a leading space, as it appears mid-sentence), or None if
    llama-server can't say. One request for all of them."""
    if not words:
        return []
    try:
        r = requests.post(f"{LLM_SERVER_URL}/tokenize", json={"content": "".join(" " + w for w in words), "with_pieces": True},
                          timeout=TIMEOUT)
        r.raise_for_status()
        toks = [t["piece"] for t in r.json()["tokens"] if isinstance(t, dict)]
    except (requests.RequestException, KeyError, ValueError, TypeError):
        return None
    out: list[list[str]] = []
    for p in toks:
        if p.startswith(" ") or not out:
            out.append([p])
        else:
            out[-1].append(p)
    # Trust it only if the pieces join back into exactly the words sent.
    if [("".join(ps)).strip() for ps in out] != words:
        return None
    return out
