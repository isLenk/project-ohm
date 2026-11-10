"""The two kinds of backend: an OpenAI-compatible HTTP API, and a CLI (Codex, Claude Code) run as a subprocess.

Both have complete(task) -> text, which raises on any failure so the router can try the next one, and keep the
token counts of this thread's last call in last_usage.
"""

import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

import requests

CLI_TIMEOUT = 240         # seconds; a packed judge batch of 30 can take a couple of minutes
CLI_PARALLEL = 3          # at most this many calls at once per CLI, so a subscription isn't rate limited
ADAPTERS_TTL_S = 15       # how long llama-server's adapter list is trusted
CLAUDE_SYSTEM_MAX = 8000  # a longer system prompt goes on stdin; Windows command lines are capped at 32k

IMAGE_EXTENSIONS = {"image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}  # anything else is a .jpg

TOKENS_USED = re.compile(r"tokens used\s*:?\s*([\d,]+)", re.IGNORECASE)


class OpenAIBackend:
    def __init__(self, name, url, model=None, key_env=None, lora_off=False, timeout=(5, 120)):
        self.name = name
        self.url = url.rstrip("/")
        self.server = self.url[: -len("/v1")] if self.url.endswith("/v1") else self.url
        self.model = model
        self.key_env = key_env
        self.lora_off = lora_off
        self.timeout = timeout
        self.cloud = key_env is not None
        self.needs_gpu_swap = name == "qwen"
        self._adapters = (0.0, None)
        self._local = threading.local()

    @property
    def last_usage(self) -> dict:
        return getattr(self._local, "usage", {})

    def ready(self) -> bool:
        """False when an API key is needed and isn't set."""
        return not self.key_env or bool(os.getenv(self.key_env))

    def _adapters_off(self):
        """Every loaded LoRA at scale 0, so utility calls run on the base model whichever character is on
        (like the Curator's llm._adapters_off). None when llama-server can't list them."""
        at, ids = self._adapters
        if time.monotonic() - at > ADAPTERS_TTL_S:
            try:
                r = requests.get(f"{self.server}/lora-adapters", timeout=2)
                ids = [a["id"] for a in r.json()] if r.ok else None
            except (requests.RequestException, ValueError, KeyError, TypeError):
                ids = None
            self._adapters = (time.monotonic(), ids)
        return [{"id": i, "scale": 0.0} for i in ids] if ids else None

    def complete(self, task) -> str:
        body = {"messages": [{"role": "system", "content": task.system},
                             {"role": "user", "content": self.content(task)}],
                "max_tokens": task.max_tokens, "temperature": task.temperature}
        if self.model:
            body["model"] = self.model
        if task.schema:
            if self.cloud:
                body["response_format"] = {"type": "json_object"}
            else:
                # ? llama-server turns the schema into a grammar, so the answer can't come out malformed
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": task.schema}}
        if self.lora_off:
            off = self._adapters_off()
            if off:
                body["lora"] = off
        headers = {"Authorization": f"Bearer {os.getenv(self.key_env)}"} if self.key_env else {}
        timeout = (self.timeout[0], task.timeout) if task.timeout else self.timeout
        r = requests.post(f"{self.url}/chat/completions", json=body, headers=headers, timeout=timeout)
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        usage = data.get("usage") or {}
        self._local.usage = {"prompt_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}
        return (data["choices"][0]["message"].get("content") or "").strip()

    def content(self, task):
        """The user message: just the text, or the text and an image_url part per picture (what llama-server
        with --mmproj and OpenRouter's vision models take)."""
        if not task.images:
            return task.user
        if self.name == "deepseek":
            raise RuntimeError("deepseek doesn't take images")
        return [{"type": "text", "text": task.user}] + [
            {"type": "image_url", "image_url": {"url": as_data_url(image)}} for image in task.images]


class CliBackend:
    """Codex or Claude Code, run in an empty temp folder so they can't read the repo or anything else of ours."""

    cloud = True
    needs_gpu_swap = False

    def __init__(self, name, model=None, reasoning=None, timeout=CLI_TIMEOUT):
        self.name = name
        self.model = model
        self.reasoning = reasoning
        self.timeout = timeout
        self._slots = threading.Semaphore(CLI_PARALLEL)
        self._local = threading.local()

    @property
    def last_usage(self) -> dict:
        return getattr(self._local, "usage", {})

    @property
    def path(self):
        return shutil.which(self.name)

    def ready(self) -> bool:
        return self.path is not None

    def complete(self, task) -> str:
        exe = self.path
        if not exe:
            raise RuntimeError(f"{self.name} isn't installed")
        folder = tempfile.mkdtemp(prefix="ohm-brain-")
        try:
            args, stdin = (self._codex_args if self.name == "codex" else self._claude_args)(task, folder)
            timeout = task.timeout or self.timeout
            with self._slots:
                try:
                    r = subprocess.run([exe, *args], input=stdin, capture_output=True, encoding="utf-8",
                                       errors="replace", cwd=folder, timeout=timeout, shell=False)
                except subprocess.TimeoutExpired:
                    # ? Its own message is the whole command line
                    raise RuntimeError(f"{self.name} took longer than {timeout:g} s (Task.timeout raises it)")
            if r.returncode != 0:
                raise RuntimeError(f"exit {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}")
            if self.name == "codex":
                text, usage = parse_codex(r.stdout, r.stderr, os.path.join(folder, "out.json") if task.schema else None)
            else:
                text, usage = parse_claude(r.stdout)
            self._local.usage = usage
            return text
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def _codex_args(self, task, folder):
        # ? MCP servers and the user's hooks start with every session; switching them off saves ~1-2 s a call.
        # (A -p profile would do the same, but Codex 0.163 wants it as a separate ~/.codex/<name>.config.toml.)
        args = ["exec"]
        for i, image in enumerate(task.images):
            # ? Right after "exec": -i takes one or more files, so the "-" (prompt on stdin) can't come after it
            args += ["-i", as_file(image, folder, f"image{i}")]
        args += ["--ephemeral", "--skip-git-repo-check", "-s", "read-only",
                 "-c", "mcp_servers={}", "-c", "features.hooks=false"]
        if self.model:
            args += ["-m", self.model]
        effort = task.reasoning or self.reasoning
        if effort:
            args += ["-c", f"model_reasoning_effort={effort}"]
        if task.schema:
            with open(os.path.join(folder, "schema.json"), "w", encoding="utf-8") as f:
                json.dump(strict(task.schema), f)
            args += ["--output-schema", "schema.json", "-o", "out.json"]
        return args + ["-"], f"{task.system}\n\n{task.user}"

    def _claude_args(self, task, folder):
        if task.images:
            raise RuntimeError("claude backend doesn't take images")
        args = ["-p", "--output-format", "json", "--tools", "", "--no-session-persistence", "--strict-mcp-config"]
        if self.model:
            args += ["--model", self.model]
        if task.schema:
            args += ["--json-schema", json.dumps(task.schema)]
        if len(task.system) <= CLAUDE_SYSTEM_MAX:
            return args + ["--system-prompt", task.system], task.user
        return args, f"{task.system}\n\n{task.user}"


def as_file(image: str, folder, name: str) -> str:
    """A picture as a file path: a data URL is written into `folder` (the call's temp folder) as <name>.jpg/.png."""
    if not image.startswith("data:"):
        return image
    header, _, data = image.partition(",")
    kind = header[len("data:"):].split(";")[0]
    path = os.path.join(folder, name + IMAGE_EXTENSIONS.get(kind, ".jpg"))
    with open(path, "wb") as f:
        f.write(base64.b64decode(data))
    return path


def as_data_url(image: str) -> str:
    """A picture as a data URL: a file path is read and encoded, a data URL is kept as it is."""
    if image.startswith("data:"):
        return image
    kind = mimetypes.guess_type(image)[0] or "image/jpeg"
    with open(image, "rb") as f:
        return f"data:{kind};base64,{base64.b64encode(f.read()).decode()}"


def parse_codex(stdout: str, stderr: str = "", out_file=None):
    """The reply and token count from `codex exec`. The reply is on stdout (or in -o's file when a schema
    was given); "tokens used" and the count come after it, on stderr in 0.163 but on stdout in older builds."""
    text = None
    if out_file and os.path.exists(out_file):
        with open(out_file, encoding="utf-8", errors="replace") as f:
            text = f.read().strip()
    if not text:
        text = re.split(r"^\s*tokens used\b", stdout or "", flags=re.IGNORECASE | re.MULTILINE)[0].strip()
    if not text:
        raise RuntimeError("codex gave no reply")
    m = TOKENS_USED.search(f"{stdout}\n{stderr}")
    # ? Codex only gives the total (its own ~10k-token system prompt included), so it's counted as prompt tokens
    return text, {"prompt_tokens": int(m.group(1).replace(",", "")) if m else None, "output_tokens": None}


def parse_claude(stdout: str):
    """The reply and token counts from `claude -p --output-format json`."""
    envelope = json.loads(stdout)
    if envelope.get("is_error"):
        raise RuntimeError(str(envelope.get("result") or envelope.get("subtype"))[:300])
    usage = envelope.get("usage") or {}
    prompt = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    if envelope.get("structured_output") is not None:
        text = json.dumps(envelope["structured_output"])
    else:
        text = (envelope.get("result") or "").strip()
    return text, {"prompt_tokens": prompt or None, "output_tokens": usage.get("output_tokens")}


def strict(schema):
    """The schema as OpenAI's structured outputs want it: every object closed, with all its keys required."""
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    if isinstance(out.get("properties"), dict):
        out["properties"] = {k: strict(v) for k, v in out["properties"].items()}
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    if isinstance(out.get("items"), dict):
        out["items"] = strict(out["items"])
    return out
