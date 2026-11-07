# API

`curator_service` listens on `http://127.0.0.1:8200`. It binds to localhost only, so it needs no auth. All bodies are JSON. Times are UTC milliseconds. Long-running work streams progress over Server-Sent Events (SSE).

Errors always have this shape:

```json
{ "error": "parent_not_earlier", "message": "Replies can only point at earlier messages." }
```

Every mutating route returns `"dirty": true` so the UI can show "Unsaved changes".

## Project

| Method | Path | Body / query | Returns |
|---|---|---|---|
| POST | `/project/new` | `{ path, name }` | project info |
| POST | `/project/open` | `{ path }` | project info, or `409 recovery_available` with the working copy's timestamp |
| POST | `/project/open` | `{ path, recover: true \| false }` | project info |
| POST | `/project/save` | — | `{ saved_at }` |
| POST | `/project/save-as` | `{ path }` | `{ saved_at, path }` |
| GET | `/project` | — | `{ path, name, dirty, saved_at, settings, counts }` |
| PATCH | `/project/settings` | any of `split_gap_ms, block_gap_ms, suggest_high, suggest_low, author_cap, character` | settings |

Changing `split_gap_ms` re-derives conversations that have `manual = 0`. It clears approval on any conversation whose boundaries move.

## Import

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/imports` | `{ paths: [...] }` or `{ path }`: files and/or folders (the Electron main process shows the file picker) | a job; its `result` is the import summary (`import.md`) |
| GET | `/imports` | — | `{ sources }`, each with `imported_messages` |
| GET | `/jobs/{id}/events` | — | SSE for imports, embedding and memory extraction: `{ kind, done, total, note }` |

## Feed

One endpoint serves both the Threads and the Raw view. Raw just ignores the link fields.

`GET /feed?channel_id=&cursor=&direction=down|up&limit=200`

- `cursor` is a message id. Omit it to start at the oldest message, or pass `around=<message id>` to centre on a message (used by search and conversation jumps).
- Pages break at block boundaries, never mid-block.

```json
{
  "items": [
    { "type": "conversation_start", "conversation": { "id": 12, "first_ts": 0, "last_ts": 0,
      "approved": false, "archived": false, "people": 5, "messages": 10,
      "skew": [{ "author_id": "...", "share": 0.45 }], "unattached": 1, "suggested": 2 } },
    { "type": "block", "id": "881", "author_id": "...", "color_index": 4,
      "link": { "parent_id": "...", "status": "accepted", "origin": "discord",
                "parent_ts": 0, "parent_preview": null },
      "messages": [
        { "id": "...", "ts": 0, "content_text": "Mb I was out all day and just got home",
          "excluded": false, "mentions": [], "link": null },
        { "id": "...", "ts": 0, "content_text": "I’m down to baro", "excluded": false,
          "link": { "parent_id": "...", "status": "accepted", "origin": "manual" } }
      ] }
  ],
  "authors": { "<id>": { "name": "KaptainDoosh", "display_name": null, "color_index": 4, "avatar_url": "..." } },
  "next_cursor": "...", "prev_cursor": "..."
}
```

`parent_ts` and `parent_preview` are filled only when the parent is not in this page.

## Links

| Method | Path | Body | Notes |
|---|---|---|---|
| PUT | `/links/block/{block_id}` | `{ parent_id }` or `{ root: true }` | drag block / mark thread start |
| PUT | `/links/message/{message_id}` | `{ parent_id }` or `{ root: true }` | drag a line, which splits it |
| DELETE | `/links/block/{block_id}` | — | click arrow to detach |
| DELETE | `/links/message/{message_id}` | — | detach, or merge a split line back into its block |
| POST | `/links/{unit_kind}/{unit_id}/accept` | — | ✓ on a suggestion |
| POST | `/links/{unit_kind}/{unit_id}/reject` | — | ✕ on a suggestion; may return a new suggestion |
| POST | `/links/guess` | `{ conversation_id? }` | job: the local LLM guesses parents for unattached blocks. `503 llm_down` if llama-server is off. Result `{ blocks, suggested, no_reply, failed, stopped, units }` |

Every link route returns:

```json
{ "units": [ /* changed units, same shape as in /feed */ ],
  "conversations_changed": [ { "id": 12, "approved": false } ],
  "inverse": { "method": "PUT", "path": "/links/block/881", "body": { "parent_id": "..." } },
  "dirty": true }
```

The UI pushes `inverse` onto its undo stack. Ctrl+Z replays it.

## Messages

| Method | Path | Body |
|---|---|---|
| PATCH | `/messages/{id}` | `{ excluded: bool }` |
| GET | `/messages/{id}/context` | — returns the message's thread path to the root (used by the training preview) |

## Conversations

| Method | Path | Body / query | Notes |
|---|---|---|---|
| GET | `/conversations` | `?filter=all\|approved\|archived` | sidebar list: `id, first_ts, last_ts, people, messages, author colors, approved, archived` |
| POST | `/conversations/{id}/approve` | `{ confirm?: true }` | Accepts the conversation's suggested links. `409 needs_confirm` with `{ suggested, unattached }` until confirmed, only when some blocks are unattached |
| POST | `/conversations/{id}/unapprove` | — | |
| POST | `/conversations/{id}/archive` | — | |
| POST | `/conversations/{id}/unarchive` | — | |
| POST | `/conversations/split` | `{ before_message_id }` | "Split here" in the Raw view |
| POST | `/conversations/{id}/merge-up` | — | "Merge with above" |

## Search

`GET /search?q=&mode=text|regex&limit=50` — see `search.md` for the grammar and result shape.

## People and memories

| Method | Path | Body / query |
|---|---|---|
| GET | `/people` | — authors with message counts, share, memory counts by status |
| GET | `/people/{author_id}/memories` | `?status=new\|kept\|forgotten` |
| POST | `/people/{author_id}/memories` | `{ category, text }` (Add memory) |
| PATCH | `/memories/{id}` | `{ text?, status?, linked_author_id? }` |
| POST | `/people/{author_id}/memories/keep-all` | — |
| POST | `/memories/sync` | — push kept, unsynced memories to `memory_service` |

## Models (registry, app-wide)

| Method | Path | Body |
|---|---|---|
| GET | `/models` | — `{ base_models: [...], adapters: [...], active: { base_model_id, adapter_id } }` |
| POST | `/models/base` | `{ gguf_path, name?, hf_path? }` → hash, inspect GGUF metadata, register |
| POST | `/models/base/hf` | `{ repo_id }` → download in the background; returns a job id |
| PATCH | `/models/base/{id}` | `{ name?, hf_path? }` |
| POST | `/models/adapters` | `{ path, base_model_id, name? }` → import an existing LoRA |
| PUT | `/models/active` | `{ base_model_id, adapter_id? }` → what the dropdown selects; also what training uses |

### llama-server (the Engine panel's Model half)

| Method | Path | Body |
|---|---|---|
| GET | `/llm/status` | — `{ running, ready, pid, uptime_s, model_path, adapters, active, vram: { used_gb, total_gb } }` |
| POST | `/llm/start` | — job. Starts llama-server on the active model, with the command line it last ran with (or like `_run_llama.cmd`). `409 llm_running`, `no_model`, `no_llama` |
| POST | `/llm/stop` | — `{ running: false }`. Frees the VRAM; the command line is kept for the next start |
| POST | `/llm/restart` | — job. Same model and flags, with every adapter for the active base loaded. `409 llm_not_running` |

## Export and training

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/export/preview` | — | `{ examples, conversations, by_author: [...], after_cap: [...], dropped: { excluded, unattached, short, "too long", bots, capped }, sample: [messages, ...] }` |
| POST | `/export/jsonl` | `{ path }` | `{ path, examples, sha256 }` |
| POST | `/train` | `{ config?: {...} }` | `{ run_id }`; `409` if a run is active or the base has no `hf_path` |
| GET | `/train/runs` | — | runs from the registry |
| GET | `/train/runs/{id}` | — | run row, plus the last progress event |
| GET | `/train/runs/{id}/events` | — | SSE stream (`training.md`); reconnect any time |
| POST | `/train/runs/{id}/cancel` | — | |

## Health

`GET /health` → `{ ok, project_open, gpu: { name, vram_gb } | null, llama_server: bool, memory_service: bool }`
