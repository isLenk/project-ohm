"""curator_service: the Conversation Curator backend. See Documentation/Curator/api.md.

Run from the repo root:
    python -m uvicorn curator_service.app.main:app --host 127.0.0.1 --port 8200

POST /imports runs importer.py as a background job (Documentation/Curator/import.md).
"""

import json
import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import (conversations, curate, embeddings, export, feed, gpu, importer, jobs, llm, memories, model_links, names,
               reflect, search, terms, threads)
from .config import MODELS_DIR, PORT, TRAIN_SIMULATE
from .errors import ApiError
from .project import Project, require_project, state
from .registry import get_registry
from .train import checks, evaluate, runs

TOKEN = os.getenv("CURATOR_TOKEN", "")

names.register()  # names the brain masks for cloud models

app = FastAPI(title="Ohm Curator", version="1")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(null|file://.*|https?://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def check_token(request: Request, call_next):
    """Electron passes a per-launch token so other local pages can't drive this service."""
    if TOKEN and request.method != "OPTIONS" and request.url.path != "/health":
        given = request.headers.get("x-curator-token") or request.query_params.get("token")
        if not given or not secrets.compare_digest(given, TOKEN):
            return JSONResponse({"error": "bad_token", "message": "Missing or wrong curator token."}, status_code=401)
    return await call_next(request)


@app.exception_handler(ApiError)
async def api_error(_: Request, e: ApiError):
    return JSONResponse(e.body(), status_code=e.status)


@app.on_event("startup")
def startup() -> None:
    runs.resume(get_registry())
    reflect.start()  # polls the NAS for reflections to run; does nothing without MEMORY_API_URL


def dirty(body: Any) -> Any:
    if isinstance(body, dict):
        body = {**body, "dirty": state.project.dirty if state.project else False}
    return body


def sse(gen) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _maybe_embed(project: Project) -> None:
    if embeddings.available() and project.scalar(
        "SELECT COUNT(*) FROM messages m LEFT JOIN message_embeddings e ON e.message_id = m.id WHERE e.message_id IS NULL"
    ):
        jobs.start("embeddings", lambda job: embeddings.build(project, job))


# ── health ──────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    status = gpu.status()
    # pid: so the app can restart a service it can't use (one started with a token it doesn't know)
    return {"ok": True, "pid": os.getpid(), "project_open": state.project is not None,
            "gpu": {"name": status["name"], "vram_gb": status["total_gb"]} if status else None,
            "llama_server": llm.up(), "memory_service": bool(memories.MEMORY_API_URL),
            "embeddings": embeddings.available(), "simulate_training": TRAIN_SIMULATE}


# ── project ─────────────────────────────────────────────────────────────────
class NewProject(BaseModel):
    path: str
    name: Optional[str] = None


class OpenProject(BaseModel):
    path: str
    recover: Optional[bool] = None


class PathBody(BaseModel):
    path: str


def _swap(project: Project) -> dict:
    if state.project is not None:
        state.project.close()
    state.project = project
    terms.share_soon(project)  # terms defined before the bot could read them, or while the NAS was down
    reg = get_registry()
    recent = [str(project.path)] + [r for r in reg.get("recent", []) if r != str(project.path)]
    reg.put("recent", recent[:8])
    embeddings.invalidate()
    _maybe_embed(project)
    return project.info()


@app.post("/project/new")
def project_new(b: NewProject):
    path = Path(b.path)
    if path.suffix != ".ohm":
        path = path.with_suffix(".ohm")
    return _swap(Project.create(path, b.name or path.stem))


@app.post("/project/open")
def project_open(b: OpenProject):
    if state.project is not None and Path(b.path) == state.project.path:
        return state.project.info()
    return _swap(Project.open(Path(b.path), b.recover))


@app.post("/project/save")
def project_save():
    return {"saved_at": require_project().save(), "path": str(state.project.path), "dirty": False}


@app.post("/project/save-as")
def project_save_as(b: PathBody):
    path = Path(b.path)
    if path.suffix != ".ohm":
        path = path.with_suffix(".ohm")
    return {"saved_at": require_project().save(path), "path": str(path), "dirty": False}


@app.post("/project/close")
def project_close():
    if state.project is not None:
        state.project.close()
        state.project = None
    return {"ok": True}


@app.get("/project")
def project_get():
    if state.project is None:
        return {"open": False, "recent": get_registry().get("recent", [])}
    return {"open": True, **state.project.info()}


class Settings(BaseModel):
    split_gap_ms: Optional[int] = None
    block_gap_ms: Optional[int] = None
    suggest_high: Optional[float] = None
    suggest_low: Optional[float] = None
    author_cap: Optional[float] = None
    character: Optional[str] = None
    name: Optional[str] = None


@app.patch("/project/settings")
def project_settings(b: Settings):
    p = require_project()
    values = {k: v for k, v in b.model_dump().items() if v is not None}
    merged = {**p.settings(), **values}
    if merged["split_gap_ms"] < merged["block_gap_ms"]:
        raise ApiError(422, "bad_gap", "The conversation gap must be at least the block gap.")
    if not 0 < merged["author_cap"] <= 1:
        raise ApiError(422, "bad_cap", "Author cap is a share between 0 and 1.")
    with p.tx() as conn:
        p.set_settings(conn, values)
        if "split_gap_ms" in values:
            for ch in feed.channels(conn):
                conversations.rebuild(conn, ch["channel_id"], merged)
    return dirty(p.info())


# ── imports ─────────────────────────────────────────────────────────────────
class ImportBody(BaseModel):
    path: Optional[str] = None
    paths: Optional[list[str]] = None


@app.post("/imports")
def imports_post(b: ImportBody):
    """Start an import job. Files and folders; DiscordChatExporter JSON, messages.jsonl, speakers.json."""
    p = require_project()
    paths = b.paths or ([b.path] if b.path else [])
    if not paths:
        raise ApiError(422, "nothing_to_import", "Pick at least one file.")
    importer.expand(paths)  # missing files fail now, not inside the job

    def work(job):
        result = importer.run(p, paths, job)
        embeddings.invalidate()
        _maybe_embed(p)
        return result

    return jobs.start("import", work)


@app.get("/imports")
def imports_get():
    p = require_project()
    with p.lock:
        return {"sources": importer.listing(p.conn)}


# ── feed ────────────────────────────────────────────────────────────────────
@app.get("/channels")
def channels_get():
    p = require_project()
    with p.lock:
        return {"channels": feed.channels(p.conn)}


@app.get("/feed")
def feed_get(channel_id: Optional[str] = None, cursor: Optional[str] = None, direction: str = "down",
             around: Optional[str] = None, limit: int = 60):
    p = require_project()
    with p.lock:
        return feed.page(p.conn, channel_id, cursor, direction, around, limit)


@app.get("/authors")
def authors_get():
    p = require_project()
    with p.lock:
        return {"authors": feed.authors(p.conn)}


# ── links ───────────────────────────────────────────────────────────────────
class LinkBody(BaseModel):
    parent_id: Optional[str] = None
    root: bool = False


class RestoreBody(BaseModel):
    unit_kind: str
    unit_id: str
    link: Optional[dict] = None


def _kind(kind: str) -> str:
    if kind not in ("block", "message"):
        raise ApiError(404, "not_found", "Unit kind is block or message.")
    return kind


@app.put("/links/{kind}/{unit_id}")
def link_put(kind: str, unit_id: str, b: LinkBody):
    p = require_project()
    with p.tx() as conn:
        return threads.set_parent(conn, _kind(kind), unit_id, b.parent_id, b.root)


@app.delete("/links/{kind}/{unit_id}")
def link_delete(kind: str, unit_id: str):
    p = require_project()
    with p.tx() as conn:
        return threads.delete(conn, _kind(kind), unit_id)


@app.post("/links/{kind}/{unit_id}/accept")
def link_accept(kind: str, unit_id: str):
    p = require_project()
    with p.tx() as conn:
        return threads.accept(conn, _kind(kind), unit_id)


@app.post("/links/{kind}/{unit_id}/reject")
def link_reject(kind: str, unit_id: str):
    p = require_project()
    settings = p.settings()
    with p.tx() as conn:
        return threads.reject(conn, _kind(kind), unit_id, settings)


@app.post("/links/restore")
def link_restore(b: RestoreBody):
    p = require_project()
    with p.tx() as conn:
        return threads.restore(conn, _kind(b.unit_kind), b.unit_id, b.link)


@app.post("/links/suggest")
def link_suggest_all():
    """Re-run the suggester over every block that has no link yet."""
    p = require_project()
    settings = p.settings()
    with p.tx() as conn:
        return dirty(threads.suggest_all(conn, settings))


class GuessBody(BaseModel):
    conversation_id: Optional[int] = None


@app.post("/links/guess")
def link_guess(b: GuessBody):
    """Background job: the local LLM picks parents for blocks the suggester left unattached."""
    p = require_project()
    if not llm.up():
        raise ApiError(503, "llm_down", "Start llama-server (Ohm's LLM on :5000) to have the model guess replies.")
    # Separate kinds, so a conversation's button doesn't attach to a running project-wide job.
    kind = "link_guess" if b.conversation_id is not None else "link_guess_all"
    return jobs.start(kind, lambda job: model_links.guess(p, job, b.conversation_id))


@app.get("/links/guess/pending")
def link_guess_pending():
    """How many blocks a project-wide guess run would ask the model about."""
    return {"blocks": model_links.pending_count(require_project())}


@app.get("/stats/suggester")
def suggester_stats():
    p = require_project()
    with p.lock:
        return threads.suggester_stats(p.conn)


# ── messages ────────────────────────────────────────────────────────────────
class MessagePatch(BaseModel):
    excluded: bool


@app.patch("/messages/{message_id}")
def message_patch(message_id: str, b: MessagePatch):
    p = require_project()
    with p.tx() as conn:
        msg = threads.message(conn, message_id)
        conn.execute("UPDATE messages SET excluded = ?, exclude_reason = ? WHERE id = ?",
                     (int(b.excluded), "manual" if b.excluded else None, message_id))
        changed = threads.unapprove(conn, threads.conversations_of(conn, [msg]))
        return {"units": [feed.block_item(conn, msg["block_id"], None)], "conversations_changed": changed,
                "inverse": {"method": "PATCH", "path": f"/messages/{message_id}", "body": {"excluded": bool(msg["excluded"])}},
                "dirty": True}


@app.get("/messages/{message_id}/context")
def message_context(message_id: str):
    p = require_project()
    with p.lock:
        return {"path": threads.thread_path(p.conn, message_id)}


# ── conversations ───────────────────────────────────────────────────────────
class ApproveBody(BaseModel):
    confirm: bool = False


class SplitBody(BaseModel):
    before_message_id: str


@app.get("/conversations")
def conversations_list(filter: str = "all"):
    p = require_project()
    with p.lock:
        return {"conversations": conversations.listing(p.conn, filter)}


@app.get("/conversations/candidates")
def conversations_candidates(min_messages: Optional[int] = None, min_people: Optional[int] = None,
                             max_gap_ms: Optional[int] = None, max_media: Optional[float] = None,
                             min_attached: Optional[float] = None, min_repliers: Optional[int] = None):
    """Unapproved conversations that look good enough to approve in bulk. Criteria left out use the defaults."""
    p = require_project()
    with p.lock:
        r = conversations.candidates(p.conn, min_messages=min_messages, min_people=min_people, max_gap_ms=max_gap_ms,
                                     max_media=max_media, min_attached=min_attached, min_repliers=min_repliers)
    for s in r["passed"]:
        del s["reasons"]
    return {"criteria": r["criteria"], "defaults": conversations.CRITERIA, "candidates": r["passed"],
            "replies": r["replies"], "skipped": r["skipped"], "reasons": conversations.REASONS,
            "approved": r["approved"]}


class BulkBody(BaseModel):
    ids: list[int]


class BulkUndoBody(BaseModel):
    ids: list[int]
    accepted: list[dict] = []


@app.post("/conversations/approve-bulk")
def conversations_approve_bulk(b: BulkBody):
    """Approve many at once (like approve with confirm). Send the result to unapprove-bulk to undo it."""
    p = require_project()
    with p.tx() as conn:
        out = conversations.approve_many(conn, b.ids)
    return dirty(out)  # after the commit, which is what marks the project dirty


class CurateBody(BaseModel):
    criteria: dict = {}
    approve: bool = True  # False: only return the kept ids


@app.post("/conversations/curate")
def conversations_curate(b: CurateBody):
    """Background job: approve the bulk candidates the brain's curate role keeps"""
    p = require_project()
    return jobs.start("curate", lambda job: curate.run(p, job, b.criteria, b.approve))


@app.post("/conversations/unapprove-bulk")
def conversations_unapprove_bulk(b: BulkUndoBody):
    """Undo approve-bulk: {ids: what it approved, accepted: the arrows it accepted}."""
    p = require_project()
    with p.tx() as conn:
        out = conversations.unapprove_many(conn, b.ids, b.accepted)
    return dirty(out)


@app.get("/conversations/{cid}")
def conversation_get(cid: int):
    p = require_project()
    with p.lock:
        return conversations.stats(p.conn, conversations.get(p.conn, cid))


@app.post("/conversations/{cid}/approve")
def conversation_approve(cid: int, b: ApproveBody):
    p = require_project()
    with p.tx() as conn:
        return dirty(conversations.approve(conn, cid, b.confirm))


@app.post("/conversations/{cid}/unapprove")
def conversation_unapprove(cid: int):
    p = require_project()
    with p.tx() as conn:
        return dirty(conversations.set_flag(conn, cid, "approved_at", False))


@app.post("/conversations/{cid}/archive")
def conversation_archive(cid: int):
    p = require_project()
    with p.tx() as conn:
        return dirty(conversations.set_flag(conn, cid, "archived_at", True))


@app.post("/conversations/{cid}/unarchive")
def conversation_unarchive(cid: int):
    p = require_project()
    with p.tx() as conn:
        return dirty(conversations.set_flag(conn, cid, "archived_at", False))


@app.post("/conversations/split")
def conversation_split(b: SplitBody):
    p = require_project()
    with p.tx() as conn:
        return dirty({"conversations": conversations.split(conn, b.before_message_id)})


@app.get("/recent")
def recent_get():
    return {"recent": [r for r in get_registry().get("recent", []) if Path(r).exists()]}


@app.post("/conversations/{cid}/merge-up")
def conversation_merge(cid: int):
    p = require_project()
    with p.tx() as conn:
        return dirty(conversations.merge_up(conn, cid))


# ── search ──────────────────────────────────────────────────────────────────
@app.get("/search")
def search_get(q: str = "", mode: str = "text", limit: int = 50):
    return search.run(require_project(), q, mode, min(max(limit, 1), 200))


# ── people & memories ───────────────────────────────────────────────────────
class MemoryNew(BaseModel):
    category: str = "fact"
    text: str


class MemoryPatch(BaseModel):
    text: Optional[str] = None
    status: Optional[str] = None
    linked_author_id: Optional[str] = None


class ExtractBody(BaseModel):
    conversation_id: Optional[int] = None


@app.get("/people")
def people_get():
    p = require_project()
    with p.lock:
        return {"people": memories.people(p.conn)}


@app.get("/people/{author_id}/memories")
def memories_get(author_id: str, status: Optional[str] = None):
    p = require_project()
    with p.lock:
        return {"memories": memories.listing(p.conn, author_id, status)}


@app.post("/people/{author_id}/memories")
def memories_add(author_id: str, b: MemoryNew):
    p = require_project()
    with p.tx() as conn:
        mid = memories.add(conn, author_id, b.category, b.text, status="kept")
        return dirty(memories._memory_out(conn, memories.get(conn, mid)))


@app.post("/people/{author_id}/memories/keep-all")
def memories_keep_all(author_id: str, sure: bool = False):
    """sure=true keeps only the sure bets."""
    p = require_project()
    with p.tx() as conn:
        return dirty({"kept": memories.keep_all(conn, author_id, sure_only=sure)})


@app.patch("/memories/{memory_id}")
def memory_patch(memory_id: int, b: MemoryPatch):
    p = require_project()
    with p.tx() as conn:
        return dirty(memories.update(conn, memory_id, b.text, b.status, b.linked_author_id))


class MergeBody(BaseModel):
    ids: list[int]


@app.post("/memories/{memory_id}/merge")
def memory_merge(memory_id: int, b: MergeBody):
    p = require_project()
    with p.tx() as conn:
        return dirty(memories.merge(conn, memory_id, b.ids))


@app.get("/messages/{message_id}/around")
def message_around(message_id: str, before: int = 8, after: int = 4):
    p = require_project()
    with p.lock:
        return memories.around(p.conn, message_id, min(max(before, 0), 50), min(max(after, 0), 50))


# ── general terminology ─────────────────────────────────────────────────────
class TermBody(BaseModel):
    term: str
    definition: Optional[str] = None
    relation: Optional[str] = None
    related_term: Optional[str] = None


class TermPatch(BaseModel):
    definition: Optional[str] = None
    relation: Optional[str] = None
    related_term: Optional[str] = None
    ignored: Optional[bool] = None


@app.get("/terms")
def terms_get():
    p = require_project()
    with p.lock:
        out = {"terms": terms.listing(p.conn), "counts": terms.counts(p.conn)}
    out["shared"] = terms.shared_status(p)
    return out


@app.post("/terms")
def terms_save(b: TermBody):
    p = require_project()
    with p.tx() as conn:
        out = dirty(terms.save(conn, b.term, b.definition, b.relation, b.related_term))
    terms.share_soon(p)
    return out


@app.post("/terms/scan")
def terms_scan():
    p = require_project()
    with p.tx() as conn:
        return dirty(terms.scan(conn))


@app.patch("/terms/{term_id}")
def term_patch(term_id: int, b: TermPatch):
    p = require_project()
    with p.tx() as conn:
        out = dirty(terms.update(conn, term_id, b.definition, b.relation, b.related_term, b.ignored))
    terms.share_soon(p)
    return out


@app.delete("/terms/{term_id}")
def term_delete(term_id: int):
    p = require_project()
    with p.tx() as conn:
        terms.delete(conn, term_id)
        out = dirty({"deleted": term_id})
    terms.share_soon(p)
    return out


class SuggestBody(BaseModel):
    ids: Optional[list[int]] = None  # just these terms (asked again even if they have a suggestion)


@app.post("/terms/suggest")
def terms_suggest(b: SuggestBody):
    """Background job: the local model suggests a meaning for every unknown term without one."""
    p = require_project()
    if not llm.up():
        raise ApiError(503, "llm_down", "Start llama-server (Ohm's LLM on :5000) to suggest definitions.")
    return jobs.start("terms", lambda job: terms.suggest(p, job, b.ids))


@app.post("/terms/accept-all")
def terms_accept_all():
    p = require_project()
    with p.tx() as conn:
        out = dirty({"accepted": terms.accept_all(conn)})
    terms.share_soon(p)
    return out


@app.post("/terms/{term_id}/accept")
def term_accept(term_id: int):
    p = require_project()
    with p.tx() as conn:
        out = dirty(terms.accept(conn, term_id))
    terms.share_soon(p)
    return out


@app.delete("/terms/{term_id}/suggestion")
def term_dismiss(term_id: int):
    p = require_project()
    with p.tx() as conn:
        return dirty(terms.dismiss(conn, term_id))


@app.post("/terms/share")
def terms_share():
    """Send the defined terms to memory_service now (they also go after every change)."""
    return terms.share(require_project())


@app.post("/memories/extract")
def memories_extract(b: ExtractBody):
    p = require_project()
    if not llm.up():
        raise ApiError(503, "llm_down", "Start llama-server (Ohm's LLM on :5000) to pull memories from messages.")
    return jobs.start("memories", lambda job: memories.extract(p, job, b.conversation_id))


@app.post("/memories/sync")
def memories_sync():
    p = require_project()
    character = p.setting("character")
    with p.tx() as conn:
        out = dirty(memories.sync(conn, character))
    out["terms"] = terms.share(p)
    return out


# ── jobs ────────────────────────────────────────────────────────────────────
@app.get("/jobs")
def jobs_list():
    return {"jobs": jobs.listing()}


@app.post("/jobs/embeddings")
def jobs_embeddings():
    p = require_project()
    if not embeddings.available():
        raise ApiError(503, "no_embeddings", "Install sentence-transformers to turn on similarity search.")
    return jobs.start("embeddings", lambda job: embeddings.build(p, job))


@app.get("/jobs/{job_id}")
def job_get(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise ApiError(404, "not_found", "Job not found.")
    return j


@app.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    jobs.cancel(job_id)
    return {"ok": True}


@app.get("/jobs/{job_id}/events")
def job_events(job_id: str):
    def gen():
        last = None
        while True:
            j = jobs.get(job_id)
            if not j:
                yield f"event: error\ndata: {json.dumps({'error': 'not_found'})}\n\n"
                return
            snap = (j["done"], j["total"], j["status"], j["note"])
            if snap != last:
                yield f"event: progress\ndata: {json.dumps(j)}\n\n"
                last = snap
            if j["status"] != "running":
                return
            time.sleep(0.5)
    return sse(gen())


# ── models ──────────────────────────────────────────────────────────────────
class BaseModelBody(BaseModel):
    gguf_path: str
    name: Optional[str] = None
    hf_path: Optional[str] = None


class BasePatch(BaseModel):
    name: Optional[str] = None
    hf_path: Optional[str] = None


class HfBody(BaseModel):
    repo_id: str
    filename: Optional[str] = None


class AdapterBody(BaseModel):
    path: str
    base_model_id: str
    name: Optional[str] = None


class ActiveBody(BaseModel):
    base_model_id: str
    adapter_id: Optional[str] = None


def _models() -> dict:
    reg = get_registry()
    out = reg.listing()
    llama = gpu.running_llama()
    out["llm"] = {"running": llm.up(), "model_path": _model_arg(llama), "adapters": gpu.loaded_adapters() or []}
    return out


def _model_arg(snap: Optional[dict]) -> Optional[str]:
    if not snap:
        return None
    cmd = snap["cmdline"]
    for i, a in enumerate(cmd):
        if a in ("-m", "--model") and i + 1 < len(cmd):
            return str((Path(snap["cwd"]) / cmd[i + 1]).resolve())
    return None


@app.get("/models")
def models_get():
    return _models()


@app.post("/models/base")
def models_add_base(b: BaseModelBody):
    get_registry().add_base(b.gguf_path, b.name, b.hf_path)
    return _models()


@app.patch("/models/base/{model_id}")
def models_patch_base(model_id: str, b: BasePatch):
    get_registry().update_base(model_id, b.name, b.hf_path)
    return _models()


@app.post("/models/base/hf")
def models_from_hf(b: HfBody):
    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError:
        raise ApiError(503, "no_hf", "Install huggingface_hub to download models.")
    files = [f for f in HfApi().list_repo_files(b.repo_id) if f.endswith(".gguf") and not Path(f).name.lower().startswith(("mmproj", "mtp"))]
    if not files:
        raise ApiError(422, "no_gguf", f"{b.repo_id} has no .gguf files.")
    filename = b.filename
    if not filename:
        if len(files) > 1:
            raise ApiError(409, "choose_file", "Pick which file to download.", files=files)
        filename = files[0]

    def download(job):
        job.progress(0, 1, f"downloading {filename}")
        path = hf_hub_download(b.repo_id, filename, local_dir=str(MODELS_DIR))
        row = get_registry().add_base(path)
        job.progress(1, 1, "done")
        return row

    return jobs.start("download", download, dedupe=False)


@app.post("/models/adapters")
def models_add_adapter(b: AdapterBody):
    get_registry().add_adapter(b.path, b.base_model_id, b.name)
    return _models()


@app.put("/models/active")
def models_active(b: ActiveBody):
    reg = get_registry()
    base = reg.base(b.base_model_id)
    adapter = reg.adapter(b.adapter_id) if b.adapter_id else None
    if adapter and adapter["base_model_id"] != base["id"]:
        raise ApiError(422, "wrong_base", "That adapter was trained for a different model.")
    reg.put("active", {"base_model_id": base["id"], "adapter_id": adapter["id"] if adapter else None})
    llama = gpu.running_llama()
    running_model = _model_arg(llama)
    same_model = bool(running_model) and Path(running_model).resolve() == Path(base["gguf_path"]).resolve()
    applied = same_model and gpu.apply_adapter(adapter["gguf_path"] if adapter else None)
    return {**_models(), "applied": applied, "needs_restart": bool(llama) and not applied}


def _active_model(reg) -> tuple[dict, dict, list[str]]:
    """The active base model, the active selection, and every adapter trained for that base."""
    act = reg.get("active", {}) or {}
    if not act.get("base_model_id"):
        raise ApiError(409, "no_model", "Pick a model first.")
    base = reg.base(act["base_model_id"])
    adapters = [a["gguf_path"] for a in reg.all("SELECT gguf_path FROM adapters WHERE base_model_id = ?", (base["id"],))]
    return base, act, adapters


def _remember(llama: Optional[dict]) -> Optional[dict]:
    """Keep the last command llama-server ran with, so Load can start it the same way after Unload."""
    if llama:
        get_registry().put("llama_last", {"name": llama["name"], "cwd": llama["cwd"], "cmdline": llama["cmdline"]})
    return llama


@app.get("/llm/status")
def llm_status():
    reg = get_registry()
    llama = _remember(gpu.running_llama())
    status = gpu.status()
    used = None
    if llama and status:
        used = next((p["used_gb"] for p in status["processes"] if p["pid"] == llama["pid"]), None)
    return {
        "running": bool(llama),
        "ready": llm.up(),
        "pid": llama["pid"] if llama else None,
        "uptime_s": gpu.uptime(llama["pid"]) if llama else None,
        "model_path": _model_arg(llama),
        "adapters": (gpu.loaded_adapters() or []) if llama else [],
        "active": reg.get("active", {"base_model_id": None, "adapter_id": None}),
        "vram": {"used_gb": used, "total_gb": status["total_gb"]} if status else None,
    }


@app.post("/llm/start")
def llm_start():
    """Start llama-server on the active model, the way it last ran (or like _run_llama.cmd)."""
    if gpu.running_llama():
        raise ApiError(409, "llm_running", "llama-server is already running.")
    reg = get_registry()
    base, act, adapters = _active_model(reg)
    snap = reg.get("llama_last") or gpu.default_llama(base["gguf_path"], adapters)
    if not Path(snap["cmdline"][0]).exists() and not shutil.which(snap["cmdline"][0]):
        raise ApiError(409, "no_llama", f"Can't find llama-server at {snap['cmdline'][0]}. Set LLAMA_CPP_DIR in Settings.")
    cmdline = gpu.llama_command(snap["cmdline"], base["gguf_path"], adapters)

    def start(job):
        job.progress(0, 2, "starting llama-server")
        if not gpu.start(snap, cmdline):
            return {"ok": False}
        ok = gpu.wait_for_llama()
        job.progress(1, 2, "applying adapter")
        if ok and act.get("adapter_id"):
            gpu.apply_adapter(reg.adapter(act["adapter_id"])["gguf_path"])
        job.progress(2, 2, "done" if ok else "llama-server did not come up")
        return {"ok": ok}

    return jobs.start("llm_start", start)


@app.post("/llm/stop")
def llm_stop():
    """Stop llama-server and free its VRAM. Its command line is kept for the next Load."""
    llama = _remember(gpu.running_llama())
    if llama:
        gpu.stop([llama["pid"]])
    return {"running": False}


@app.post("/llm/restart")
def llm_restart():
    """Restart llama-server on the active model with every adapter for it loaded."""
    reg = get_registry()
    base, act, _ = _active_model(reg)
    llama = _remember(gpu.running_llama())
    if not llama:
        raise ApiError(409, "llm_not_running", "llama-server isn't running, so there's no command to restart it with.")

    def restart(job):
        job.progress(0, 3, "stopping llama-server")
        gpu.stop([llama["pid"]])
        adapters = [a["gguf_path"] for a in reg.all("SELECT gguf_path FROM adapters WHERE base_model_id = ?", (base["id"],))]
        job.progress(1, 3, "starting llama-server")
        gpu.start(llama, gpu.llama_command(llama["cmdline"], base["gguf_path"], adapters))
        ok = gpu.wait_for_llama()
        job.progress(2, 3, "applying adapter")
        if ok and act.get("adapter_id"):
            gpu.apply_adapter(reg.adapter(act["adapter_id"])["gguf_path"])
        job.progress(3, 3, "done" if ok else "llama-server did not come back")
        return {"ok": ok}

    return jobs.start("llm_restart", restart)


# ── export & training ───────────────────────────────────────────────────────
class TrainBody(BaseModel):
    config: Optional[dict] = None
    stop_pids: list[int] = []


@app.get("/export/preview")
def export_preview():
    return export.preview(require_project())


@app.post("/export/jsonl")
def export_jsonl(b: PathBody):
    return export.write(require_project(), Path(b.path))


@app.get("/gpu")
def gpu_get():
    return {"gpu": gpu.status()}


@app.get("/train/preflight")
def train_preflight():
    return runs.preflight(require_project(), get_registry(), runs.DEFAULT_CONFIG)


@app.post("/train")
def train_start(b: TrainBody):
    return runs.start(require_project(), get_registry(), b.config, b.stop_pids)


@app.get("/train/runs")
def train_runs():
    reg = get_registry()
    return {"runs": reg.all("SELECT * FROM train_runs ORDER BY started_at DESC LIMIT 50"), "active": runs.active(reg)}


@app.get("/train/runs/{run_id}")
def train_run(run_id: str):
    return runs.get(get_registry(), run_id)


@app.get("/train/runs/{run_id}/events")
def train_events(run_id: str):
    reg = get_registry()
    runs.get(reg, run_id)
    return sse(runs.stream(reg, run_id))


@app.post("/train/runs/{run_id}/cancel")
def train_cancel(run_id: str):
    return runs.cancel(get_registry(), run_id)


@app.post("/train/check-replies")
def train_check_replies():
    """Job `check_replies`: the judge reads every reply the export would train on and says whether it answers
    the line before it (train/checks.py). The export leaves out the ones that don't."""
    project, reg = require_project(), get_registry()
    todo = checks.prepare(project, reg)
    return jobs.start("check_replies", lambda job: checks.run(project, reg, todo, job))


@app.get("/eval/{adapter_id}")
def eval_get(adapter_id: str):
    """The last evaluation of an adapter, or {"exists": false}."""
    reg = get_registry()
    adapter = reg.adapter(adapter_id)
    row = reg.one("SELECT run_dir FROM train_runs WHERE id = ?", (adapter.get("run_id"),)) if adapter.get("run_id") else None
    saved = evaluate.load(Path(row["run_dir"])) if row else None
    # ? judge_ready doesn't check the Codex sign-in (that runs the CLI); POST /eval does, and says so with no_judge
    return {"exists": True, **saved} if saved else {"exists": False, "trained_here": bool(row),
                                                     "judge": evaluate.judge_name() or evaluate.JUDGE_MODEL.stem,
                                                     "judge_ready": evaluate.judge_problem(check_sign_in=False) is None}


@app.post("/eval/{adapter_id}")
def eval_start(adapter_id: str):
    """Score an adapter with the brain's judge, as a job (train/evaluate.py). Only stops and restarts llama-server
    when the judge is the local Qwen model."""
    reg = get_registry()
    prep = evaluate.prepare(reg, adapter_id)
    return jobs.start("eval", lambda job: evaluate.run(reg, prep, job))


# ── reflection ──────────────────────────────────────────────────────────────
@app.get("/reflect/status")
def reflect_status():
    """{polling, last_poll_at, running: reflection id or null, last_result} (reflect.py)."""
    return reflect.status()


@app.post("/reflect/run-now")
def reflect_run_now():
    """Look for a queued reflection now instead of waiting for the next poll. Runs in the background."""
    if not reflect.MEMORY_API_URL:
        raise ApiError(503, "memory_service_off", "Set MEMORY_API_URL in the root .env to run reflections.")
    if reflect.status()["running"] is not None:
        raise ApiError(409, "reflect_running", "A reflection is already running.")
    reflect.poll_soon()
    return reflect.status()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
