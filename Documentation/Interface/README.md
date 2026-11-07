# Interface (controller-mainframe)

The Electron app that runs Ohm: Dashboard, Characters, Curator, Modules, Memories, Filters, Settings, and the Engine panel behind the power button at the bottom of the rail. It looks like the Curator: near-black ground, hairline dividers, one clay accent. The design canvas is here:
https://claude.ai/artifact/TvApSAwDzbNVRAgxEpBYST (private until Lance shares it).

## What runs where

```
Electron main (controller-mainframe/src/main)
  ├── engine.js     starts/stops main.py, notices one started by hand, records start/stop/crash
  ├── api.js        :3001. Modules (from modules/*/body.json) and the heartbeat main.py sends
  ├── health.js     uptime, restarts and errors from events.db
  ├── settings.js   settings.json in the app data folder → env for main.py and curator_service
  ├── nas.js        the memory service; the renderer reaches it through the 'nas' IPC handler
  └── curator.js    starts curator_service on :8200
curator_service     llama-server status/start/stop/restart (/llm/*), the model registry
memory_service      (NAS) characters, the message log, memories, lore, knowledge, dashboard stats
main.py             Discord and the modules; reports health every 5 s (pyhandler.start_heartbeat)
```

## Engine

Two processes, two halves of the panel:

- **Model** is llama-server. Load, Unload and Reload go to `curator_service` (`/llm/start`, `/llm/stop`, `/llm/restart`), which already knew how to restart it around training. Load starts it with the command line it was last seen running with, so start it once with `_run_llama.cmd` (that's where `--mmproj` comes from). Before that, it uses the same flags as the script, without `--mmproj`.
- **Python side** is `main.py`, started by the app with the repo's venv. A `main.py` started with `_run_script.cmd` shows up through its heartbeat as "started outside the app". Stop ends it, but that script starts it again on its own.

Playing a character card puts its base model and LoRA on llama-server: the adapter is switched live when the base is already loaded, otherwise llama-server restarts on the new base.

## Modules

A module is a folder in `modules/` with a `body.json` (`name`, `description`, `required`). The app reads them from disk, so they're listed while `main.py` is off.

- **On/off** is saved as `OHM_DISABLED_MODULES` and read by `controller/modules.py` when `main.py` starts. Required modules have no switch. Memory off: nothing is recalled or remembered, but characters and the message log still use the NAS. Speech to text or text to speech off: Ohm doesn't join voice.
- **Health** comes from `main.py` every 5 s: `POST /api/modules/heartbeat { pid, modules: { name: { ok, detail, off? } } }`. Every change of `ok` is an event in `events.db`.
- **Restart** is queued and handed to `main.py` in the reply to its next heartbeat (`{ restart: [names] }`). Everything runs in one process, so restart means: Memory reconnects to the NAS, Text to speech makes new clients for its server, and Discord and Speech to text rejoin the voice call.

## Uptime

`events.db` holds `{ kind: 'engine' | 'module', name, event, at }`. The engine is up from `start` to the next `stop` or `crash`. A module is up from `up` to the next `down` or `off`, and only counts while the engine is up. Time the app wasn't open isn't counted either way. The 30-day strip marks a day `down` under 90% up, `restarted` when there was a restart or a failure, `off` when the module was turned off and never ran.

## Dashboard numbers

All from the memory service's message log (`/stats`, `/people`, `/conversations`), so they need Settings › Memory set. Reply time is from when the message Ohm answered was logged to when his reply was. That includes speech to text and thinking time, not the time spent speaking. Conversations split wherever a channel went quiet for 30 minutes, the same as the Curator.

## Settings

Every field is an environment variable the Python side or `curator_service` already reads. Saved values go to `settings.json` and are passed to `main.py` and `curator_service` when they start. A variable set in the real environment wins and is locked on the page. `DATABASE_URL`, `EMBED_MODEL` and `MODEL_CACHE` belong to the memory service's `.env` on the NAS, so they aren't here.
