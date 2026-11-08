"""Runs a Task on the first backend of its role that answers, falling back down the list.

One call, many items: a judge with 2,314 replies to check doesn't make 2,314 calls. pack() numbers 20-50 of them
into one Task whose answer is {"answers": [{"n": 1, ...}, ...]}, unpack() lines the answers back up with the
items, and run_packed() does both and re-asks anything missing once, in smaller batches.
"""

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, log
from .task import Task

RETRY_DIVISOR = 3       # missing answers are asked again in batches this many times smaller
TOKENS_PER_ITEM = 60    # max_tokens a packed Task gets per item, on top of PACK_TOKENS
PACK_TOKENS = 100

_local = threading.local()  # which backend answered this thread's last run(), see last_backend()

TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
         "boolean": bool, "null": type(None)}


def skip_reason(name: str, local_only: bool = False):
    """Why backend `name` can't be used right now, or None when it can."""
    b = config.backend(name)
    if b is None:
        return "not a backend"
    if config.is_cloud(name) and (local_only or config.local_only()):
        return "local only"
    if name in config.PAID and config.over_cap():
        return "over today's spend cap"
    ready = getattr(b, "ready", None)
    if ready and not ready():
        return "not installed or no key"
    return None


def run(task: Task):
    """The answer to `task`: text, or the parsed JSON when it has a schema. None if every backend failed."""
    started = time.perf_counter()
    _local.backend = None
    for name in config.chain(task.role):
        why = skip_reason(name, task.local_only)
        if why:
            if why == "not a backend":
                print(f"[brain] '{name}' in BRAIN_ROLE_{task.role.upper()} isn't a backend")
            continue
        b = config.backend(name)
        t = time.perf_counter()
        try:
            text = b.complete(task)
            answer = parse(text, task.schema) if task.schema else text
            if answer is None:
                raise ValueError(f"the answer doesn't fit the schema: {text[:120]!r}")
        except Exception as e:
            print(f"[brain] {name} failed for {task.role}: {e}")
            continue
        usage = getattr(b, "last_usage", None) or {}
        usd = config.cost(name, usage)
        config.add_spend(usd)
        log.report(task, name, round((time.perf_counter() - t) * 1000), True, text, usage, usd)
        _local.backend = name
        return answer
    log.report(task, None, round((time.perf_counter() - started) * 1000), False)
    return None


def last_backend():
    """The backend that answered this thread's last run(), or None when nothing did."""
    return getattr(_local, "backend", None)


def run_many(tasks: list, workers: int = 4) -> list:
    """run() over every task at once, answers in the same order. Keep workers at 3 or less for the CLIs
    (subscription limits); the HTTP APIs are fine with 8-16."""
    if not tasks:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(tasks)))) as pool:
        return list(pool.map(run, tasks))


def needs_gpu_swap(role: str) -> bool:
    """True when `role` would run on the local judge model first, so the Curator has to stop Ohm's
    llama-server to make room for it."""
    for name in config.chain(role):
        if not skip_reason(name):
            return bool(getattr(config.backend(name), "needs_gpu_swap", False))
    return False


# ── JSON answers ─────────────────────────────────────────────────────────────
def parse(text: str, schema: dict):
    """The JSON in `text` (the whole thing, or the first {...} / [...] in it) if it fits `schema`, else None."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        m = re.search(r"(\{.*\}|\[.*\])", text or "", re.DOTALL)
        try:
            value = json.loads(m.group(1)) if m else None
        except ValueError:
            value = None
    return value if value is not None and fits(value, schema) else None


def fits(value, schema: dict) -> bool:
    """A shallow check: the right type, the required keys there, and each top-level key of the right type."""
    if not _is(value, schema.get("type")):
        return False
    if isinstance(value, dict):
        if any(k not in value for k in schema.get("required", [])):
            return False
        for k, sub in (schema.get("properties") or {}).items():
            if k in value and not _is(value[k], (sub or {}).get("type")):
                return False
    return True


def _is(value, kind) -> bool:
    if kind is None:
        return True
    if isinstance(kind, list):
        return any(_is(value, k) for k in kind)
    if kind in ("integer", "number") and isinstance(value, bool):  # ? True is an int in Python
        return False
    return isinstance(value, TYPES.get(kind, object))


# ── Many items in one call ───────────────────────────────────────────────────
def batched(items: list, size: int) -> list:
    size = max(1, size)
    return [items[i:i + size] for i in range(0, len(items), size)]


def pack(items: list, system: str, instructions: str, schema_item: dict, size: int = 30, role: str = "judge",
         **fields) -> list:
    """One Task per `size` items. Each item (already a string) is numbered from 1 within its Task, and the
    answer has to be {"answers": [one schema_item per item, each with "n" = its number]}. `fields` go on
    every Task (local_only, reasoning, temperature...)."""
    item = dict(schema_item)
    item["properties"] = {"n": {"type": "integer"}, **(schema_item.get("properties") or {})}
    item["required"] = ["n", *[k for k in schema_item.get("required", []) if k != "n"]]
    schema = {"type": "object", "properties": {"answers": {"type": "array", "items": item}}, "required": ["answers"]}
    tasks = []
    for batch in batched(items, size):
        numbered = "\n\n".join(f"### {i}\n{text}" for i, text in enumerate(batch, 1))
        user = (f"{instructions}\n\n{numbered}\n\nAnswer all {len(batch)} items as "
                f'{{"answers": [...]}}, one answer per item in order, with "n" set to the item\'s number.')
        tasks.append(Task(role=role, system=system, user=user, schema=schema, items=len(batch),
                          max_tokens=fields.get("max_tokens", PACK_TOKENS + TOKENS_PER_ITEM * len(batch)),
                          **{k: v for k, v in fields.items() if k != "max_tokens"}))
    return tasks


def unpack(result, n: int, schema_item: dict | None = None) -> list:
    """A packed Task's answers lined up with its n items, None where one is missing or doesn't fit."""
    out = [None] * n
    answers = result.get("answers") if isinstance(result, dict) else result if isinstance(result, list) else []
    for pos, a in enumerate(answers or []):
        if not isinstance(a, dict):
            continue
        i = a.get("n") if isinstance(a.get("n"), int) and not isinstance(a.get("n"), bool) else pos + 1
        if 1 <= i <= n and out[i - 1] is None and (schema_item is None or fits(a, schema_item)):
            out[i - 1] = a
    return out


def run_packed(items: list, system: str, instructions: str, schema_item: dict, size: int = 30, workers: int = 3,
               retry: bool = True, **fields) -> list:
    """An answer per item (None where there isn't one), asking in packs of `size`. Items left without an
    answer are asked once more in packs of size // 3, since a smaller pack is less likely to skip one."""
    tasks = pack(items, system, instructions, schema_item, size=size, **fields)
    out = []
    for task, result in zip(tasks, run_many(tasks, workers)):
        out += unpack(result, task.items, schema_item)
    missing = [i for i, a in enumerate(out) if a is None]
    if retry and missing and size > 1:
        print(f"[brain] asking again for {len(missing)} of {len(items)} items")
        again = run_packed([items[i] for i in missing], system, instructions, schema_item,
                           size=max(1, size // RETRY_DIVISOR), workers=workers, retry=False, **fields)
        for i, a in zip(missing, again):
            out[i] = a
    return out
