# Brain

A small package that sends Ohm's one-shot LLM jobs (prompt in, text or JSON out) to whichever backend each
job's role is set to, and falls back down the list when one fails. The Codex CLI (a ChatGPT subscription) is the
main cloud backend; Ohm's own llama-server and the local judge model are the private ones.

It's a uv workspace member, so the engine and the Curator both get it with `uv sync --all-packages`.

```python
from brain import Task, run

note = run(Task("extract", system, f"{speaker}: {text}", max_tokens=40))            # text, or None
verdict = run(Task("judge", system, prompt, schema={"type": "object", "properties": {"yes": {"type": "boolean"}},
                                                    "required": ["yes"]}))         # dict, or None
```

`python -m brain status` prints what's installed and signed in (for Settings › Integrations), and
`python -m brain try <role> "<text>"` runs a tiny task and says which backend answered.

## Settings

Everything is an env var (the app writes them to the root `.env`); the defaults are in `brain/config.py`.

| Variable | Default | What it does |
| --- | --- | --- |
| `BRAIN_ROLE_<ROLE>` | see below | Backends to try for a role, in order, e.g. `BRAIN_ROLE_JUDGE=codex,qwen` |
| `BRAIN_LOCAL_ONLY` | off | `1` = only `local` and `qwen`, for every role. Nothing leaves the PC |
| `BRAIN_CODEX_MODEL` | Codex's own config | Model for `codex` |
| `BRAIN_CODEX_REASONING` | `medium` | `low` / `medium` / `high` for `codex` (a Task's `reasoning` wins) |
| `BRAIN_CLAUDE_MODEL` | `haiku` | Model for `claude` |
| `DEEPSEEK_API_KEY`, `BRAIN_DEEPSEEK_MODEL` | `deepseek-chat` | `deepseek` |
| `OPENROUTER_API_KEY`, `BRAIN_OPENROUTER_MODEL` | none | `openrouter` |
| `BRAIN_DAILY_USD` | `0.50` | Daily spend cap for the paid APIs (deepseek, openrouter); kept in `brain-spend.json` in the Curator data folder |
| `CURATOR_LLM_URL`, `CURATOR_JUDGE_PORT` | `:5000/v1`, `5055` | Where `local` and `qwen` are |

Default roles: classify `local`, extract `local`, brief `codex,local`, curate `codex,local`,
judge `codex,qwen`, reflect `codex,deepseek`, label `codex`, screen `codex`.

## Backends

| Name | What | Trivial call | Notes |
| --- | --- | --- | --- |
| `local` | Ohm's llama-server | - | Every LoRA at scale 0, so it's the base model |
| `qwen` | The judge llama-server (Qwen 27B) | - | Needs Ohm's llama-server stopped: `brain.needs_gpu_swap(role)` |
| `codex` | `codex exec` (ChatGPT subscription) | ~4.3 s (5.4 s with MCP and hooks on) | About 10k tokens a call of its own |
| `claude` | `claude -p` (Claude subscription) | ~3 s, ~4.8 s with a schema | |
| `deepseek` | DeepSeek API | - | Paid, counts against the cap |
| `openrouter` | OpenRouter API | - | Paid, counts against the cap |

Timings (a "Reply with OK" prompt, Python wall clock) measured on 2026-11-07 (codex-cli 0.163.0, Claude Code 2.1.319). A packed judge task of 4 items took
3.9 s on Codex and 3.1 s on Claude. `local` and `qwen` weren't running, so they aren't measured yet.

The CLIs run in an empty temp folder (removed after), read-only and without tools, so they can't see the repo.
Codex runs with `-c mcp_servers={}` and `-c features.hooks=false`. Those flags do what a profile would, and
Codex 0.163 wants a profile as a separate `~/.codex/<name>.config.toml` anyway. At most 3 calls run at once
per CLI.

## Adding a role

Set `BRAIN_ROLE_<NAME>=codex,local` and make Tasks with `role="<name>"`. Add a line to `DEFAULT_ROLES` in
`config.py` so it has a default and shows up in `status()`.

## Many items in one call

Don't make one call per item: Codex alone spends ~4 s and ~10k tokens a call. Pack 20-50 items into one call:

```python
item = {"type": "object", "properties": {"answers": {"type": "boolean"}}, "required": ["answers"]}
verdicts = brain.run_packed(texts, JUDGE_SYSTEM, "For each item, does the reply answer the line before it?",
                            item, size=30, workers=3, role="judge")
# one dict (or None) per text, in the same order
```

`pack()` numbers the items (`### 1`, `### 2`...) into Tasks that want `{"answers": [{"n": 1, ...}, ...]}`, and
`unpack()` puts the answers back in order, with None for any missing or malformed answer. `run_packed()` does both,
then asks for the missing ones once more in packs of `size // 3`. Use `pack`/`unpack` yourself if you need to
handle the retry differently.

## Images

`Task(..., images=[...])` shows pictures with the prompt, as file paths or data URLs. `codex` gets each one as
`-i <file>` (a data URL is written into the call's temp folder first), and `local` and `openrouter` get them as
`image_url` parts of the user message (llama-server needs `--mmproj`, OpenRouter a vision model). `claude` and
`deepseek` raise, so the router moves on to the next backend. The `screen` role is the engine's opt-in screen
description (`OHM_SCREEN_CLOUD=1`, `modules/vision/describe.py`); it has no local fallback because live replies
already look at the screen through the local model. `brain.last_backend()` says which backend answered this
thread's last `run()`.

## Privacy

Anything with a screenshot, or text that shouldn't leave the PC, gets `Task(..., local_only=True)`, which skips
every cloud backend for that call. The one exception is the `screen` role, which only runs when the owner turned
it on. `BRAIN_LOCAL_ONLY=1` skips every cloud backend for everything, that one included.

**Names never go to a cloud backend** (`privacy.py`). Before `run()` hands a Task to anything but `local` or
`qwen`, its system prompt and user text are masked:

- Every name a known person goes by (display name, handle, preferred name, aliases) becomes one made-up first
  name, drawn fresh for every call, so two calls can't be linked by a stand-in.
- Emails, links, Discord ids, old-style Discord tags (`name#1234`), IP addresses and phone numbers become
  `[email-1]`, `[link-1]`, `[id-1]`, `[tag-1]`, `[ip-1]`, `[phone-1]`.
- The answer comes back with the real names and originals put back, parsed JSON included, so briefs and
  memory notes name the right people.

The apps say who's known with `brain.privacy.add_people(fn)`: the engine registers the NAS's users
(`controller/memory.py`), the Curator the open project's human authors plus the NAS's users (`app/names.py`).
**While no list has anybody in it, cloud backends are skipped** and the role falls through to `local`/`qwen`, so a
NAS that's down can't let real names out. `Task(..., people=[[...]])` adds people for one call. Only
`Task(..., private=False)` goes out unmasked; it's for text with nobody's details in it (`python -m brain try`).

What it can't catch: someone mentioned who isn't in any list (add their names as aliases on the Memories
page), and pictures (`images`), which are sent as they are. Cloud training goes through the same masking:
`export.for_cloud()` in the Curator, with stand-ins drawn per conversation.

## Logs

Every call prints `[brain] <role> via <backend>: <ms> ms`, and when `MEMORY_API_URL` is set, posts a row to the
NAS's `/logs` (source `brain`: role, backend, ms, tokens, cost, a preview of the answer) and a timing to `/metrics`.

## Tests

```
.venv/Scripts/python.exe -m pytest apps/brain/tests -q
```
