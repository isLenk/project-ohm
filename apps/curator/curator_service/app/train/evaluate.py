"""Scores a trained adapter on conversations it never trained on, with a bigger model as the judge.

It runs in two passes (Documentation/Curator/training.md, "Scoring an adapter"):

1. Replies: llama-server on the adapter's base model answers every held-out context three ways:
   the base model alone, the adapter at full strength, and the adapter at PARTIAL_SCALE.
   A few probes shaped like the messages that went wrong live ("can you hear me?") get the same.
2. Judge: the brain's `judge` role (Codex by default). For each context it sees the candidates, plus
   the person's real reply, unlabeled and shuffled, and says which ones answer the last message.
   Then, for the voice, it gets each candidate next to the real reply and picks the one a person
   wrote. Asked to rank candidates instead, it put the base model's polished jokes above the
   group's real replies, so ranking measured wit, not likeness. The chats go 30 to a call.

Only when the judge is the local Qwen model (brain.needs_gpu_swap) does Ohm's llama-server stop
for the judge pass, since both don't fit on a 24 GB card. It's put back the way it was either way.
The result is saved as eval.json in the run folder.
"""

import json
import os
import random
import re
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import brain
from brain import backends as brain_backends
import requests

from .. import gpu, llm
from ..config import JUDGE_MODEL, JUDGE_PORT, LLAMA_CPP_DIR, LLM_URL
from ..errors import ApiError
from .worker import held_out

PARTIAL_SCALE = 0.6
TEMPERATURE = 1.0      # what the bot replies with (controller/wrappers/openai.py)
REPLY_TOKENS = 150
MAX_ITEMS = 60
CONTEXT_CHARS = 3000   # of chat shown to the judge, the latest part
JUDGE_CTX = 16384
JUDGE_SLOTS = 4        # requests the Qwen judge serves at once; JUDGE_CTX is split between them (4096 each)
JUDGE_TEMPERATURE = 0  # verdicts, not replies
PACK_SIZE = 30         # chats per judge call: Codex spends ~4 s and ~10k tokens a call however small it is
PACK_WORKERS = brain_backends.CLI_PARALLEL  # calls at once, the most the brain lets one CLI run
SEED = 7
VARIANTS = ["base", "adapter", "partial"]
PROBES = [
    "Can you actually hear me?",
    "Tell me something interesting.",
    "what are you up to tonight",
    "what game should we play later",
    "@Ohm you there?",
    "why are you being so weird today",
    "whats your honest opinion on pineapple pizza",
    "Tell me tell me something very uh unique and interesting.",
]
LABEL = re.compile(r"^([^:\n]{1,40}): ", re.MULTILINE)

JUDGE_RULES = """You judge replies in a Discord group chat between friends.

You get the recent chat, then candidate replies from "Ohm", one member of the chat, labeled A, B, C and so on.

For each candidate, decide whether it answers the LAST message in the chat. It counts if it responds to it, reacts to it, or answers its question, even briefly or rudely. It does not count if it ignores the last message, changes the subject, or carries on some other thread."""

JUDGE_SYSTEM = JUDGE_RULES + """

Answer with JSON only, no other text:
{"answers": {"A": true, "B": false}}"""

REAL_RULES = """You read a Discord group chat between friends, then two possible next messages from "Ohm", one member of the chat, labeled A and B.

One of them is what the real person actually wrote. The other was written by an AI imitating them. Pick the one the real person wrote: look at their slang, typos, tone, length and what a friend in this chat would actually say. Polish, neat jokes and helpful explanations are signs of the AI."""

REAL_SYSTEM = REAL_RULES + """

Answer with JSON only, no other text:
{"real": "A"}"""

# The packed versions (many chats in one call): brain.run_packed wraps each item's answer in {"answers": [...]}.
# ? The candidates' verdicts are a list, not {"A": true}: Codex's structured output wants every key of an
# object named in the schema, and the number of candidates differs between chats.
SEPARATE = "Each numbered item is a separate chat with its own candidates; judge each one on its own."
JUDGE_INSTRUCTIONS = (f"{SEPARATE} For each item, \"answers\" is one true or false per candidate, in label order "
                      "(A, B, C...): true when that candidate answers the last message of its chat.")
JUDGE_ITEM = {"type": "object", "properties": {"answers": {"type": "array", "items": {"type": "boolean"}}},
              "required": ["answers"]}
REAL_INSTRUCTIONS = f"{SEPARATE} For each item, \"real\" is the label (A or B) of the reply the real person wrote."
REAL_ITEM = {"type": "object", "properties": {"real": {"type": "string", "enum": ["A", "B"]}}, "required": ["real"]}


def path_for(run_dir: Path) -> Path:
    return Path(run_dir) / "eval.json"


def load(run_dir: Path) -> Optional[dict]:
    p = path_for(run_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ── what to score ───────────────────────────────────────────────────────────
def items(run_dir: Path, seed: int) -> list[dict]:
    """The last Ohm turn of each held-out example (context, real reply), then the probes."""
    rows = [json.loads(line) for line in (run_dir / "dataset.jsonl").read_text(encoding="utf-8").splitlines() if line]
    held = held_out(rows, seed)
    if not held:
        raise ApiError(409, "nothing_held_out",
                       "This run trained on every conversation (it had under 20), so there's nothing it hasn't seen to score it on.")
    out = []
    for r in rows:
        if r.get("conversation_id") not in held:
            continue
        msgs = r["messages"]
        if msgs[-1]["role"] != "assistant":
            continue
        out.append({"kind": "held_out", "id": r["id"], "speaker": r["speaker"], "context": msgs[:-1],
                    "real": msgs[-1]["content"]})
    random.Random(SEED).shuffle(out)
    out = out[:MAX_ITEMS]
    system = rows[0]["messages"][0]
    names = Counter(n for it in out for m in it["context"] if m["role"] == "user" for n in LABEL.findall(m["content"]))
    who = names.most_common(1)[0][0] if names else "Someone"
    out += [{"kind": "probe", "id": f"probe:{i}", "speaker": None, "context": [system, {"role": "user", "content": f"{who}: {p}"}],
             "real": None} for i, p in enumerate(PROBES)]
    return out


def transcript(context: list[dict], chars: int = CONTEXT_CHARS) -> str:
    """The chat as the judge reads it: user turns are already "Name: text", Ohm's are labeled here."""
    lines = []
    for m in context:
        if m["role"] == "user":
            lines.append(m["content"])
        elif m["role"] == "assistant":
            lines.append("\n".join(("Ohm: " if i == 0 else "") + t for i, t in enumerate(m["content"].split("\n"))))
    text = "\n".join(lines)
    if len(text) > chars:
        text = text[-chars:]
        text = text[text.find("\n") + 1:] if "\n" in text else text
    return text


def judge_prompt(chat: str, candidates: list[tuple[str, str]]) -> str:
    shown = "\n\n".join(f"{label}:\n{text}" for label, text in candidates)
    return f"Chat (the last line is the one to answer):\n{chat}\n\nCandidate replies from Ohm:\n\n{shown}"


def _json(text: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def answers_of(answer: Optional[dict], labels: list[str]) -> Optional[dict[str, bool]]:
    """{label: answers the last message} from the judge's JSON, either {"answers": {"A": true}} or the packed
    {"answers": [true, ...]} in label order. None when it doesn't fit."""
    answers = (answer or {}).get("answers")
    if isinstance(answers, list):
        answers = dict(zip(labels, answers)) if len(answers) == len(labels) else None
    if not isinstance(answers, dict) or not all(isinstance(answers.get(k), bool) for k in labels):
        return None
    return {k: answers[k] for k in labels}


def parse_answers(text: str, labels: list[str]) -> Optional[dict[str, bool]]:
    """{label: answers the last message} or None when the judge's answer doesn't fit."""
    return answers_of(_json(text), labels)


def pick_of(answer: Optional[dict], labels: list[str]) -> Optional[str]:
    pick = (answer or {}).get("real")
    return pick if pick in labels else None


def parse_pick(text: str, labels: list[str]) -> Optional[str]:
    """The label the judge thinks the real person wrote, or None."""
    return pick_of(_json(text), labels)


def summarize(results: list[dict]) -> dict:
    """Per variant: share of replies that answer the last message, and share the judge took for the real
    person's when shown next to the real reply (50% means it can't tell them apart)."""
    out: dict = {}
    for kind in ("held_out", "probe"):
        group = [r for r in results if r["kind"] == kind and r.get("answers")]
        stats = {}
        for v in VARIANTS + ["real"]:
            answered = [r["answers"][v] for r in group if v in r["answers"]]
            if not answered:
                continue
            stats[v] = {"answers": round(sum(answered) / len(answered), 3), "n": len(answered)}
            passed = [r["passes"][v] for r in results
                      if r["kind"] == kind and (r.get("passes") or {}).get(v) is not None]
            if passed:
                stats[v]["passes_as_real"] = round(sum(passed) / len(passed), 3)
        out[kind] = {"items": len(group), "skipped": sum(r["kind"] == kind and not r.get("answers") for r in results),
                     "variants": stats}
    return out


# ── servers ─────────────────────────────────────────────────────────────────
def _model_arg(llama: Optional[dict]) -> Optional[str]:
    cmd = (llama or {}).get("cmdline") or []
    for i, a in enumerate(cmd[:-1]):
        if a in ("-m", "--model"):
            return cmd[i + 1]
    return None


def _same(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a and b) and Path(a).resolve() == Path(b).resolve()


def _ohm_server(reg, base: dict, adapter: dict) -> dict:
    """llama-server on the adapter's base model with every adapter for it loaded. Returns its lora ids."""
    paths = [a["gguf_path"] for a in reg.all("SELECT gguf_path FROM adapters WHERE base_model_id = ?", (base["id"],))]
    llama = gpu.running_llama()
    loaded = gpu.loaded_adapters() if llama else None
    if not (llama and _same(_model_arg(llama), base["gguf_path"])
            and any(_same(a.get("path"), adapter["gguf_path"]) for a in loaded or [])):
        if llama:
            gpu.stop([llama["pid"]])
        snap = llama or reg.get("llama_last")
        if not snap or gpu.is_judge(snap["cmdline"]):
            snap = gpu.default_llama(base["gguf_path"], paths)
        if not gpu.start(snap, gpu.llama_command(snap["cmdline"], base["gguf_path"], paths)) or not gpu.wait_for_llama(180):
            raise RuntimeError("Ohm's llama-server didn't start.")
        loaded = gpu.loaded_adapters() or []
    me = next((a["id"] for a in loaded if _same(a.get("path"), adapter["gguf_path"])), None)
    if me is None:
        raise RuntimeError("llama-server didn't load the adapter.")
    return {"ids": [a["id"] for a in loaded], "me": me}


def _lora(ids: dict, scale: float) -> list[dict]:
    return [{"id": i, "scale": scale if i == ids["me"] else 0.0} for i in ids["ids"]]


def _reply(context: list[dict], lora: list[dict], seed: int) -> str:
    r = requests.post(f"{LLM_URL}/chat/completions", timeout=(5, 120), json={
        "messages": context, "temperature": TEMPERATURE, "max_tokens": REPLY_TOKENS, "seed": seed, "lora": lora})
    r.raise_for_status()
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


def _start_judge() -> int:
    if not Path(JUDGE_MODEL).is_file():
        raise RuntimeError(f"The judge model isn't at {JUDGE_MODEL}. Set CURATOR_JUDGE_MODEL.")
    exe = LLAMA_CPP_DIR / "bin" / "llama-server.exe"
    if not exe.exists():
        exe = LLAMA_CPP_DIR / "bin" / "llama-server"
    cmd = [str(exe), "-m", str(JUDGE_MODEL), "--jinja", "--reasoning", "off", "-ngl", "99", "-c", str(JUDGE_CTX),
           "-np", str(JUDGE_SLOTS), "--port", str(JUDGE_PORT)]
    pid = gpu.start({"name": "judge", "cwd": str(LLAMA_CPP_DIR)}, cmd)
    if not pid:
        raise RuntimeError("The judge model's llama-server didn't start.")
    end = time.monotonic() + 300
    while time.monotonic() < end:
        try:
            if requests.get(f"http://127.0.0.1:{JUDGE_PORT}/health", timeout=2).ok:
                return pid
        except requests.RequestException:
            pass
        time.sleep(2)
    gpu.stop([pid])
    raise RuntimeError("The judge model didn't come up in 5 minutes (out of VRAM?).")


def _ask_judge(system: str, prompt: str) -> str:
    """One question for the brain's judge, as text. "" when no backend answered, so the parsers return None."""
    task = brain.Task(role="judge", system=system, user=prompt, max_tokens=200, temperature=JUDGE_TEMPERATURE)
    return brain.run(task) or ""


def ask_packed(texts: list[str], system: str, instructions: str, schema_item: dict, job=None,
               note: str = "judging", done: int = 0, total: int = 0) -> list:
    """The judge's answer to each text (None where there isn't one), PACK_SIZE to a call. It goes
    PACK_SIZE * PACK_WORKERS texts at a time so the job's progress moves and a cancel stops it in between."""
    out = []
    step = PACK_SIZE * PACK_WORKERS
    for start in range(0, len(texts), step):
        if job is not None and job.cancelled:
            break
        out += brain.run_packed(texts[start:start + step], system, instructions, schema_item, size=PACK_SIZE,
                                workers=PACK_WORKERS, role="judge", temperature=JUDGE_TEMPERATURE)
        if job is not None:
            job.progress(done + len(out), total, f"{note} {len(out)}/{len(texts)}")
    return out + [None] * (len(texts) - len(out))


def judge_name() -> Optional[str]:
    """What judges right now, for the saved results: the Qwen model's file name, or the brain backend's name.
    None when nothing can."""
    name = llm.brain_backend("judge")
    if name == "qwen":
        return Path(JUDGE_MODEL).stem
    if name == "codex" and os.getenv("BRAIN_CODEX_MODEL"):
        return f"codex ({os.environ['BRAIN_CODEX_MODEL']})"
    return name


def judge_problem(check_sign_in: bool = True) -> Optional[str]:
    """Why the judge can't run, or None when it can. Signing in is only checked when asked, since it runs the CLIs."""
    if brain.needs_gpu_swap("judge"):
        return None if Path(JUDGE_MODEL).is_file() else f"The judge model isn't at {JUDGE_MODEL}. Set CURATOR_JUDGE_MODEL."
    name = llm.brain_backend("judge")
    if name in ("codex", "claude") and check_sign_in and (brain.status().get(name) or {}).get("signed_in") is False:
        name = None
    if name:
        return None
    return f"No judge: sign in to Codex in Settings › Integrations or put the Qwen judge model at {JUDGE_MODEL}."


def check_judge() -> None:
    problem = judge_problem()
    if problem:
        raise ApiError(409, "no_judge", problem)


def _shuffled(pairs: list[tuple[str, str]], seed: int) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """The texts under labels A, B, C... in a random order, and {label: variant}."""
    pairs = list(pairs)
    random.Random(seed).shuffle(pairs)
    labels = [chr(65 + k) for k in range(len(pairs))]
    return [(lb, t or "(no reply)") for lb, (_, t) in zip(labels, pairs)], {lb: v for lb, (v, _) in zip(labels, pairs)}


def _candidates(it: dict, seed: int):
    return _shuffled(list(it["replies"].items()) + ([("real", it["real"])] if it["real"] else []), seed)


def _pair(it: dict, variant: str, seed: int):
    return _shuffled([(variant, it["replies"][variant]), ("real", it["real"])], seed)


def judge_item(it: dict, seed: int, ask=None) -> None:
    """Fills it["answers"] {variant: bool} and, for held-out chats, it["passes"] {variant: taken for the real one}.
    One question per call; run() uses judge_all, which packs them."""
    ask = ask or _ask_judge
    chat = transcript(it["context"])
    shown, name = _candidates(it, seed)
    raw = ask(JUDGE_SYSTEM, judge_prompt(chat, shown))
    answers = parse_answers(raw, list(name))
    it["answers"] = answers and {name[k]: a for k, a in answers.items()}
    if not answers:
        it["judge_raw"] = raw[:500]
    if not it["real"]:
        return
    it["passes"] = {}
    for k, v in enumerate(VARIANTS):
        shown, name = _pair(it, v, seed * 10 + k)
        pick = parse_pick(ask(REAL_SYSTEM, judge_prompt(chat, shown)), list(name))
        it["passes"][v] = None if pick is None else name[pick] == v


def judge_all(todo: list[dict], job=None, done: int = 0, total: int = 0) -> None:
    """judge_item for every item (item i with seed SEED + i), with the questions packed PACK_SIZE to a call:
    first every "does it answer", then every "which is real"."""
    asks, picks = [], []
    for i, it in enumerate(todo):
        chat = transcript(it["context"])
        shown, name = _candidates(it, SEED + i)
        asks.append((it, name, judge_prompt(chat, shown)))
        if it["real"]:
            it["passes"] = {}
            for k, v in enumerate(VARIANTS):
                shown, name = _pair(it, v, (SEED + i) * 10 + k)
                picks.append((it, v, name, judge_prompt(chat, shown)))
    answers = ask_packed([q[-1] for q in asks], JUDGE_RULES, JUDGE_INSTRUCTIONS, JUDGE_ITEM, job, "judging", done, total)
    for (it, name, _), a in zip(asks, answers):
        got = answers_of(a, list(name))
        it["answers"] = got and {name[k]: x for k, x in got.items()}
    done += len(asks)
    chosen = ask_packed([q[-1] for q in picks], REAL_RULES, REAL_INSTRUCTIONS, REAL_ITEM, job, "real or not", done, total)
    for (it, v, name, _), a in zip(picks, chosen):
        pick = pick_of(a, list(name))
        it["passes"][v] = None if pick is None else name[pick] == v


def _restore(before: Optional[dict], reg) -> None:
    """Start Ohm's llama-server again if it was running, with the active adapter on."""
    if not before or gpu.running_llama():
        return
    if gpu.start(before, before["cmdline"]) and gpu.wait_for_llama(180):
        act = reg.get("active", {}) or {}
        if act.get("adapter_id"):
            gpu.apply_adapter(reg.adapter(act["adapter_id"])["gguf_path"])


# ── the job ─────────────────────────────────────────────────────────────────
def prepare(reg, adapter_id: str) -> dict:
    """Everything a run needs, checked before the job starts so problems come back as a clear error."""
    adapter = reg.adapter(adapter_id)
    row = reg.one("SELECT * FROM train_runs WHERE id = ?", (adapter.get("run_id"),)) if adapter.get("run_id") else None
    if not row:
        raise ApiError(409, "not_trained_here",
                       "Only adapters trained in the Curator can be scored: it needs the run's held-out conversations.")
    check_judge()
    if reg.one("SELECT id FROM train_runs WHERE status IN ('queued','running')"):
        raise ApiError(409, "run_active", "Wait for the training run to finish: they'd share the GPU.")
    run_dir = Path(row["run_dir"])
    return {"adapter": adapter, "row": row, "run_dir": run_dir, "base": reg.base(adapter["base_model_id"]),
            "todo": items(run_dir, json.loads(row["config"]).get("seed", 1337))}


def run(reg, prep: dict, job) -> dict:
    adapter, row, run_dir, base, todo = prep["adapter"], prep["row"], prep["run_dir"], prep["base"], prep["todo"]
    adapter_id = adapter["id"]
    before = gpu.running_llama()
    swap = brain.needs_gpu_swap("judge")
    judge = judge_name()
    judge_pid = None
    total = len(todo) * 2 + len(VARIANTS) * sum(1 for it in todo if it["real"])
    try:
        job.progress(0, total, "starting Ohm's model")
        ids = _ohm_server(reg, base, adapter)
        scales = {"base": 0.0, "adapter": 1.0, "partial": PARTIAL_SCALE}
        for i, it in enumerate(todo):
            if job.cancelled:
                return {"cancelled": True}
            it["replies"] = {v: _reply(it["context"], _lora(ids, s), SEED + i) for v, s in scales.items()}
            job.progress(i + 1, total, f"Ohm's replies {i + 1}/{len(todo)}")

        if swap:  # the local Qwen judge needs the card to itself
            job.progress(len(todo), total, "loading the judge model")
            llama = gpu.running_llama()
            if llama:
                gpu.stop([llama["pid"]])
            judge_pid = _start_judge()
        judge_all(todo, job, len(todo), total)
        if job.cancelled:
            return {"cancelled": True}
    finally:
        if judge_pid:
            gpu.stop([judge_pid])
        now = gpu.running_llama()
        # ? one started here, or switched to the adapter's base model for the replies: put back what was running
        if now and (not before or now["cmdline"] != before["cmdline"]):
            gpu.stop([now["pid"]])
        _restore(before, reg)

    result = {
        "adapter_id": adapter_id, "adapter_name": adapter["name"], "run_id": row["id"], "created_at": int(time.time() * 1000),
        "judge": judge, "partial_scale": PARTIAL_SCALE, "temperature": TEMPERATURE,
        "summary": summarize(todo),
        "items": [{"kind": it["kind"], "speaker": it["speaker"], "chat": transcript(it["context"])[-1200:],
                   "real": it["real"], "replies": it["replies"], "answers": it.get("answers"), "passes": it.get("passes"),
                   **({"judge_raw": it["judge_raw"]} if it.get("judge_raw") else {})} for it in todo],
    }
    path_for(run_dir).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"summary": result["summary"]}
