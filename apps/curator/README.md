# curator_service

Backend for the Conversation Curator tab in the Ohm interface. The specs live in `Documentation/Curator/`.

## Run

The Electron app starts this service on its own when it launches. It runs it from `apps/curator` with the repo's `.venv` (`.venv\Scripts\python.exe` on Windows), or `CURATOR_PYTHON` if that is set. It also passes a per-launch token, so other local pages can't call the service.

Dependencies are in `pyproject.toml` here; `uv sync --all-packages` from the repo root installs them into `.venv`. To run the service by hand while working on it, start it from `apps/curator`:

```
uv run python -m uvicorn curator_service.app.main:app --host 127.0.0.1 --port 8200
```

When the service runs without `CURATOR_TOKEN`, it accepts calls with no token. If the app finds a service already running on :8200, it uses that one.

To train on the PC with the GPU, install the extras into a separate venv. Do not install them into the repo venv: Unsloth pulls in newer torch, numpy, transformers and setuptools, which break TTS, STT and sentence-transformers. Runs use `CURATOR_TRAIN_PYTHON` if it is set, otherwise `%APPDATA%\ohm-curator\train-venv` if it exists.

```
py -3.12 -m venv %APPDATA%\ohm-curator\train-venv
set TV=%APPDATA%\ohm-curator\train-venv\Scripts\python.exe
%TV% -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
%TV% -m pip freeze | findstr /b "torch" > %TEMP%\torch-pin.txt
%TV% -m pip install -r curator_service/requirements-train.txt gguf -c %TEMP%\torch-pin.txt --extra-index-url https://download.pytorch.org/whl/cu128
```

The constraint keeps pip from swapping CUDA torch for the CPU build.

## Settings (root .env)

| Variable | Default | What |
|---|---|---|
| `CURATOR_PORT` | 8200 | Port |
| `CURATOR_DATA_DIR` | `%APPDATA%\ohm-curator` | Registry, adapters, training runs, working copies |
| `LLAMA_CPP_DIR` | `<repo>\..\llama.cpp` | Holds `convert_lora_to_gguf.py` and `models\` |
| `CURATOR_LLM_URL` | `http://127.0.0.1:5000/v1` | llama-server, used to extract memories |
| `MEMORY_API_URL`, `MEMORY_API_KEY` | | Kept memories are synced here |
| `CURATOR_TRAIN_VRAM_GB` | 11 | VRAM a run needs before it starts. Measure it on the real card. |
| `CURATOR_TRAIN_PYTHON` | `%APPDATA%\ohm-curator\train-venv` if it exists, else the service's Python | Python that runs training (needs `requirements-train.txt`) |
| `CURATOR_TRAIN_SIMULATE` | | Set it to `1` to fake training runs, for working on the UI without a GPU |

## Demo data and tests

From `apps/curator` (or `npm test` from the repo root):

```
uv run python -m curator_service.tests.seed ~/Desktop/demo.ohm   # then open it from the Curator tab
uv run pytest curator_service/tests
```

The seed script writes rows straight into the database. It is not the importer.

## Importer

`app/importer.py` is what the Import button calls (`POST /imports`). It runs as a job and accepts DiscordChatExporter JSON, `messages.jsonl` and `speakers.json` (`app/formats.py`), as files or a folder. After loading `sources`, `authors`, `blocks`, `messages` and native links in one `project.tx()`, it calls `conversations.rebuild` and `threads.suggest_all` for each channel, then starts embeddings. See `Documentation/Curator/import.md`.

### Big files (100k+ messages)

`tests/bench.py` loads 100k synthetic messages the way the importer does and times every step. The real 137k-message export imports in about 25 s. On a laptop the import itself takes about 3 s for the inserts, 0.1 s for `rebuild` and 12 s for `suggest_all`.

- Compute blocks in Python while walking the sorted messages. Insert with `executemany` (blocks, then messages, then native links). Don't run a query per message.
- Keep it to one `project.tx()`. The FTS triggers keep search up to date during the insert, so you don't need a separate rebuild step.
- Run the import as a job (`jobs.start("import", ...)`) and return the job id, the way `/memories/extract` does. Otherwise the HTTP request blocks for the whole import while it holds the project lock. `suggest_all(..., progress=job.progress)` reports progress.
- Embeddings run in the background after the import. Not measured here (sentence-transformers was not installed), but MiniLM on CPU should take minutes for 100k messages. Similarity features turn on when they finish.

Run `python -m curator_service.tests.bench` after changing anything in the import path.

## Not verified yet

The real training path in `app/train/worker.py` (Unsloth with Gemma 4 E4B, then llama.cpp conversion) has not run on a GPU. Simulated runs exercise everything around it. Expect to adjust it on the first real run.
