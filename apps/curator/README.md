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
%TV% -m pip install -r apps/curator/requirements-train.txt gguf -c %TEMP%\torch-pin.txt --extra-index-url https://download.pytorch.org/whl/cu128
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
| `CURATOR_TRAIN_VRAM_GB` | 6 GB over the base model's GGUF size (~11 for E4B, ~14 for 12B) | VRAM a run needs before it starts. Measure it on the real card. |
| `CURATOR_TRAIN_PYTHON` | `%APPDATA%\ohm-curator\train-venv` if it exists, else the service's Python | Python that runs training (needs `requirements-train.txt`) |
| `CURATOR_TRAIN_SIMULATE` | | Set it to `1` to fake training runs, for working on the UI without a GPU |
| `CURATOR_CLOUD_GPU` | `L40S` | Modal GPU for cloud training (`A100-80GB`, `H100`: faster, about the same per run) |
| `CURATOR_CLOUD_TIMEOUT_MIN` | 60 | Modal stops a cloud run after this many minutes, whatever it is doing |

## Cloud training

The Train modal can train on a rented GPU on [Modal](https://modal.com) instead of this PC (`train/cloud.py`). Ohm
stays online, a run usually costs well under a dollar, and Modal's free plan includes monthly credit.

Once:

1. Make a Modal account, then sign this PC in from the training venv (`modal` is already installed there; if not,
   `...\python.exe -m pip install modal`):

   ```
   %APPDATA%\ohm-curator\train-venv\Scripts\python.exe -m modal token new
   ```

2. In Modal's settings, set a **spending limit**.

Per run, pick **Cloud** under "Train on". What leaves the PC is `cloud_dataset.jsonl` (`export.for_cloud`): only the
messages, with everyone's names and contact details masked by the brain's `privacy.py`, and the hyperparameters.
The adapter comes back and is converted and loaded here, the same as a local run.

The GPU stops as soon as training ends:

- the run is an ephemeral Modal app, stopped when the worker leaves `app.run()` (finished, failed or cancelled);
- Cancel stops the app at once (`modal app stop`), even while it's still downloading;
- the function has a hard time limit (`CURATOR_CLOUD_TIMEOUT_MIN`), and Modal stops an ephemeral app whose PC went away;
- containers shut down 2 s after the function returns (`scaledown_window=2`).

The first run builds the environment and downloads the base model's weights into the Modal volume
`ohm-curator-hf-cache` (~22 GB for the 12B, a few dollars a month to keep); later runs reuse both. Delete it with
`modal volume delete ohm-curator-hf-cache` if you stop training in the cloud.

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
