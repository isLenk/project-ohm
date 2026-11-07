# Conversation Curator

The Curator turns exported Discord chat into LoRA training data for Ohm. You import a chat export, scroll through it, connect who-replied-to-whom with arrows, approve the conversations that are clean, and train a LoRA adapter on the approved ones. Along the way it proposes memories about each person, which can be pushed to the memory service.

This folder is the backend spec. The UI is designed. Its design canvas is here:
https://claude.ai/artifact/A8tf4deQ2rFQhWcrPCKKqb (ask Lance for access; it is private until he shares it).

## Read in this order

| File | What it covers |
|---|---|
| [data-model.md](data-model.md) | The `.ohm` project file (SQLite) and the app-wide model registry |
| [import.md](import.md) | Parsing an export into messages, blocks and conversations |
| [threading.md](threading.md) | Reply links, splits, suggestions, validation rules |
| [api.md](api.md) | HTTP + SSE endpoints the UI calls |
| [search.md](search.md) | Ctrl+K query grammar and how to run it |
| [memories.md](memories.md) | Memory extraction, review, sync to `memory_service` |
| [training.md](training.md) | Export to JSONL, LoRA training, adapter conversion, progress events |

## Vocabulary

These words mean exactly one thing everywhere in the code and docs.

| Term | Meaning |
|---|---|
| **Message** | One Discord message. One line in the UI. |
| **Block** | Consecutive messages by one author with nothing in between (see `import.md` for the exact rule). Replies are attached per block by default. |
| **Split line** | A message inside a block that has its own reply link, different from its block's. |
| **Unit** | Whatever a reply link hangs off: a whole block, or a split line. |
| **Link / arrow** | "This unit answers that message." Every unit has at most one parent. |
| **Thread start** | A unit explicitly marked as answering nobody. |
| **Unattached** | A unit with no link and not marked as a thread start. Needs a human decision. |
| **Suggested link** | A link the backend guessed. Shown dashed. Becomes accepted when the user clicks ✓. |
| **Thread** | A tree of units connected by links, rooted at a thread start. Can span conversations. |
| **Conversation** | A bookmark into the feed: a run of messages with no silence longer than the split gap (default 30 min). The user can add or remove splits. Only conversations can be approved. |
| **Approved** | The only state that lets a conversation's units become training examples. |
| **Archived** | Hidden from the default list. Not exported. |

There is no "pending" status. A conversation is either approved, archived, or neither.

## Screens and what each needs from the backend

| Screen | Needs |
|---|---|
| Threads | Paged feed of blocks with links, conversation dividers, per-conversation stats (people, message count, author skew), link edits, approve |
| Raw | Same paged feed without links. Split/merge conversations. |
| Memories | Per-person candidate memories with source messages; keep, edit, forget, link to person |
| Ctrl+K | Search over messages, people, conversations; text, regex, person filter, operators |
| Model dropdown | List of imported base models and trained adapters (app-wide, not per project); import new |
| Save menu | Save, save as, open, export approved as JSONL. Autosave later. |
| Train modal | Start a run, stream progress (stage, step, loss, elapsed, ETA), cancel, reconnect after closing |

## Architecture

```
Electron renderer (React, controller-mainframe)
        │  HTTP + SSE, localhost:8200
        ▼
curator_service  (new, Python FastAPI, runs on the PC with the GPU)
  ├── open .ohm project  → SQLite file (one per project)
  ├── registry.sqlite    → base models + adapters, app-wide
  ├── training worker    → subprocess, PEFT/TRL on HF weights
  ├── llama-server :5000 → used for memory extraction + suggestions (optional)
  └── memory_service (NAS :8100) → kept memories pushed as user_facts
```

Why a new Python service instead of the Express server in `controller-mainframe/src/main/api.js`:
- Training is Python (PEFT/TRL or Unsloth) and needs the PC's GPU.
- SQLite FTS5, regex and embeddings are simpler in Python.
- It matches `memory_service`, which is already FastAPI.

The Electron main process (`controller-mainframe/src/main/curator.js`) starts and stops `curator_service` and gives the renderer a per-launch token. The renderer (`controller-mainframe/src/renderer/src/curator/`) talks to the service directly.

## What's built

Everything in these docs is built and tested.

```
curator_service/
  app/main.py          FastAPI routes (api.md)
  app/importer.py      Import file: authors, messages, blocks, native links, then conversations + suggestions (import.md)
  app/formats.py       input formats: DiscordChatExporter JSON, messages.jsonl, speakers.json
  app/schema.py        project + registry schemas (data-model.md); the source of truth
  app/project.py       open/save .ohm through a working copy
  app/threads.py       links, validation, undo, suggester (threading.md)
  app/conversations.py stats, approval, split/merge, rebuild from silences
  app/feed.py          paged feed for Threads and Raw
  app/search.py        Ctrl+K grammar + execution (search.md)
  app/embeddings.py    optional MiniLM vectors, near-duplicates
  app/memories.py      extraction, style traits, sync (memories.md)
  app/export.py        examples, context chains, dedupe, author cap (training.md)
  app/gpu.py           VRAM check, pause/restart llama-server, live adapter switching
  app/train/runs.py    run manager, progress over SSE
  app/train/worker.py  training subprocess (Unsloth + TRL; simulate mode for UI work)
  app/registry.py      models + adapters, app-wide
  tests/               pytest, plus seed.py demo data
  tools/jsonl_to_dce.py  write a messages.jsonl dump out as DiscordChatExporter files (optional; import reads JSONL directly)
```

## Already decided

- One project = one `.ohm` file = one SQLite database. "Save" is a checkpoint of that file, which makes autosave later trivial.
- The base model list is app-wide. Every project sees every model ever imported.
- Only approved conversations produce training targets.
- Native Discord replies are imported as accepted links.
- Kept memories go to `memory_service`, not into the LoRA dataset.

## Open questions for Lance

Each has a default so work is not blocked. Change the default in code if he decides otherwise.

1. **Split gap.** Default 30 minutes, editable per project.
2. **Approving with leftovers.** Default: approving accepts remaining suggested links and drops unattached units from the export. The confirm dialog states both counts.
3. **Context across conversations.** Default: context may only come from approved conversations; the chain stops at the first unapproved message.
4. **Per-author cap.** Default 30% of examples per author, applied at export by downsampling.
5. **Which speakers to train.** Default: everyone, with the speaker name in the prompt. Optional filter to train on selected speakers only.
6. **Training weights.** The HauhauCS uncensored model only ships as GGUF, which cannot be trained. Default: train on `google/gemma-4-e4b-it`, the model it was made from, and apply the adapter to the HauhauCS GGUF. See `training.md` section 3.
