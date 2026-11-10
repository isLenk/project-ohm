# Getting started

How to set Ohm up on a Windows PC and start him. For how the pieces relate, see [Architecture](architecture.md).

## What you need

- A PC with an NVIDIA GPU. It is built and run on an RTX 3090 (24 GB) with Windows 11, Python 3.12 and CUDA 12.1.
- [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`), Node.js and ffmpeg.
- [llama.cpp](https://github.com/ggml-org/llama.cpp) in a folder next to this repo, with the Gemma GGUF and its mmproj in `llama.cpp/models`.
- Somewhere always-on to run the memory service in Docker. Here that is a NAS.

## Setup

1. Install the dependencies:

    ```
    npm run setup
    ```

    This creates `.venv` with `uv sync --all-packages` and installs the interface's packages. The Engine and the Curator share that one venv.

2. Set up the TTS API. It runs from its own Python 3.11 venv at `apps/tts/tts_api`; the deepspeed wheel it needs is in `apps/tts`.

3. Deploy the memory service to the NAS. See [Memory service](apps/memory/README.md#deploy).

4. Point the PC at it. In the root `.env`:

    ```
    MEMORY_API_URL=http://<nas-ip>:8100
    MEMORY_API_KEY=<API_KEY>
    ```

    Put the same two values in the app under Settings › Memory.

## Launch

| Command | Starts |
|---|---|
| `npm run dev:all` | llama-server, the TTS API and the interface |
| `npm run dev` | Only the interface |

Then start the Engine from the interface with the power button at the bottom of the rail. The interface starts the Curator on its own.

The launchers in `tools/` do the same by hand: `_run_llama.cmd`, `_run_tts_api.cmd`, then `_run_script.cmd`.

## Ports

| Service | Port |
|---|---|
| Interface API | 3001 |
| llama-server | 5000 |
| TTS API | 8000 |
| Memory service (NAS) | 8100 |
| Curator | 8200 |

## Tests

`npm test` runs the Curator tests.
