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
| GET | `/messages/{id}/around` | `?before=8&after=4` — the message with the channel messages just before and after it (memory source preview) |

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
| GET | `/conversations/candidates` | criteria as query params, see below | Unapproved, unarchived conversations good enough to approve in bulk |
| POST | `/conversations/curate` | `{ criteria?, approve? }` | job (`curate.py`): of the conversations passing the bulk-approve `criteria`, the brain's `curate` role keeps the ones that show how the group talks (banter, opinions, jokes) and approves them; `approve: false` only decides. Names are masked on cloud backends. Result `{ read, approved, kept: [ids], left_out, unanswered, skipped_by_criteria, backend, left_out_why }` |
| POST | `/conversations/approve-bulk` | `{ ids: [...] }` | Approve each like `approve` with `confirm: true`, in one transaction. Returns `{ approved: [ids], skipped: [ids], accepted: [restore...], dirty }`. Already approved, archived or missing ids are skipped |
| POST | `/conversations/unapprove-bulk` | `{ ids, accepted }` | Undo approve-bulk: send back its `approved` and `accepted`. Approval comes off and accepted arrows go back to suggestions, except arrows changed since. Returns `{ unapproved: [ids], restored, dirty }` |

### Finding good conversations

Approving one at a time gave ~270 training replies in an evening; a LoRA wants 1,500 or more. "Find good conversations" (the button under the conversation list) proposes conversations, you skim a random sample and reject (archive) the bad ones, then approve enough of the rest to reach a reply target.

`GET /conversations/candidates` takes any of these; left out means the default:

| Param | Default | Passes when |
|---|---|---|
| `min_messages` | 8 | kept (not excluded) messages ≥ this |
| `min_people` | 3 | people who aren't bots ≥ this |
| `max_gap_ms` | 1800000 | no silence between two kept messages longer than this |
| `max_media` | 0.25 | share of messages that are only a link (gifs are tenor links) or carry a file ≤ this. Link previews don't count |
| `min_attached` | 0.8 | share of units with an arrow, any status ≥ this |
| `min_repliers` | 2 | people who'd get at least one trainable reply ≥ this |

```json
{
  "criteria": { "min_messages": 8, ... }, "defaults": { ... },
  "candidates": [
    { "id": 12, "channel_id": "…", "first_ts": 0, "last_ts": 0, "first_message_id": "…",
      "messages": 24, "people": 4, "replies": 14, "repliers": 4, "max_gap_ms": 540000,
      "media": 0.04, "attached": 0.93, "authors": ["id", ...] }
  ],
  "replies": 59978,
  "skipped": { "too_short": 5604, "few_people": 521, "long_silence": 78, "mostly_links": 74, "unattached": 50, "few_repliers": 1 },
  "reasons": { "too_short": "too short", ... },
  "approved": { "conversations": 42, "replies": 284 }
}
```

A skipped conversation is counted under the first criterion it fails, in the order above. `replies` is about how many assistant turns the export would make (`conversations._replies`): one per run of a person's messages, minus bots, unattached messages, most one-word replies and the opening run, the same rules as `export.build`. It leaves out chunk overlap and the author cap; on the real project it was within 1–2% of the export. `authors` is the six people with the most messages, for avatars. Scoring all 8.6k conversations (130k messages) takes about 1.5 s.

## Search

`GET /search?q=&mode=text|regex&limit=50` — see `search.md` for the grammar and result shape.

## People and memories

| Method | Path | Body / query |
|---|---|---|
| GET | `/people` | — authors with message counts, share, memory counts by status |
| GET | `/people/{author_id}/memories` | `?status=new\|kept\|forgotten`. Each memory has `priority`, `group`, `confidence` (0–1) and `sure` (see memories.md) |
| POST | `/people/{author_id}/memories` | `{ category, text }` (Add memory) |
| PATCH | `/memories/{id}` | `{ text?, status?, linked_author_id? }` |
| POST | `/people/{author_id}/memories/keep-all` | `?sure=true` keeps only the sure bets |
| POST | `/memories/{id}/merge` | `{ ids }` — fold these memories into `{id}`: their sources move over, then they are deleted |
| POST | `/memories/sync` | — push kept, unsynced memories to `memory_service`, and share the terms (`terms` in the reply) |

## General terminology

See terminology.md.

| Method | Path | Body / query |
|---|---|---|
| GET | `/terms` | — `{ terms, counts, shared }`; each term has `status`, `uses`, `people`, `examples` (newest messages using it) and `suggestion`. `shared` is how the last push to `memory_service` went (`{ ok, terms, at, error }`), or null |
| POST | `/terms` | `{ term, definition?, relation?, related_term? }` — add or update by text (Ctrl+click). `""` clears a field |
| PATCH | `/terms/{id}` | `{ definition?, relation?: 'typo'\|'same', related_term?, ignored? }` |
| DELETE | `/terms/{id}` | — |
| POST | `/terms/scan` | — look for unfamiliar words; returns `{ found, added, removed }` |
| POST | `/terms/suggest` | `{ ids? }` — job (`kind: 'terms'`): the local model suggests a meaning for each unknown term without one (or just `ids`, asked again). Result `{ terms, suggested, kinds, failed, stopped }` |
| POST | `/terms/{id}/accept` | — apply the term's suggestion |
| POST | `/terms/accept-all` | — apply every waiting suggestion; returns `{ accepted }` |
| DELETE | `/terms/{id}/suggestion` | — dismiss it; the term stays unknown and isn't asked about again |
| POST | `/terms/share` | — send the defined and linked terms to `memory_service` now; returns `shared` |

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
| POST | `/train/check-replies` | — | job `check_replies`: the judge reads every reply the export would train on that it hasn't yet (`training.md` section 7). Only stops and restarts llama-server when the judge is the local Qwen model. 409 `no_judge`, `run_active`. `/train/preflight` counts them in `export.checked` |
| GET | `/eval/{adapter_id}` | — | the adapter's last score (`eval.json`), or `{exists: false, trained_here, judge, judge_ready}`. `judge` is what would judge now (`codex`, or the Qwen model's name); `judge_ready` doesn't check the Codex sign-in, the POST does |
| POST | `/eval/{adapter_id}` | — | job `eval`: score it with the judge (`training.md` section 6). Ohm's llama-server is used for the replies; it only stops for the judge when that's the local Qwen model. 409 `not_trained_here`, `no_judge`, `run_active`, `nothing_held_out` |

## Reflection

See reflection.md. The poller starts with the service when `MEMORY_API_URL` is set.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/reflect/status` | — | `{ polling, last_poll_at, running: reflection id \| null, last_result: { id, character, at, result, error } \| null }` |
| POST | `/reflect/run-now` | — | look for a queued reflection now, in the background; returns the status. 503 `memory_service_off`, 409 `reflect_running` |

## Health

`GET /health` → `{ ok, pid, project_open, gpu: { name, vram_gb } | null, llama_server: bool, memory_service: bool }`

No token needed. `pid` lets the app restart a service that rejects its token (`apps/mainframe/src/main/curator.js`).
