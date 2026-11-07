<!-- Image -->
![Logo](Documentation/Banners/OhmBanner.png)

### Virtual Assistant
Project heavily inspired by Vedals Neuro and DougDoug's bots.

Virtual Assistant is a passion project that can combines Text-To-Speech, Speech-To-Text, and Generative AI to produce human-like AI chatbots.

The project uses three models.
1. GenAI
2. TTS
3. MLLM for Computer Vision

### Features:
- Discord Integration
- Text-To-Speech: RealtimeTTS
- Voice-To-Text: FasterWhisper / Whisper on Discord
- Modularization: Toggleable abilities
- Vision: miniCPM


---
### Layout
| Folder | What it is |
| --- | --- |
| `apps/mainframe` | Electron + React interface. Starts the curator and the engine |
| `apps/engine` | The assistant: Discord bot, STT, memory and modules (`main.py`, `dev.py`) |
| `apps/curator` | Conversation Curator backend (FastAPI, port 8200) |
| `apps/memory` | Memory service (FastAPI + Postgres), runs in Docker on the NAS |
| `apps/tts` | TTS API, with its own Python 3.11 venv |
| `tools` | `.cmd` launchers and the dev runner |
| `Documentation` | Specs and banners |

The engine and curator share one venv (`.venv`), managed with [uv](https://docs.astral.sh/uv/) from the root `pyproject.toml`.

### Setup
1. Install uv (`winget install astral-sh.uv`) and Node.js
2. `npm run setup` creates `.venv` and installs the interface's packages
3. The TTS API runs from its own Python 3.11 venv at `apps/tts/tts_api` (the deepspeed wheel it needs is in `apps/tts`)

### How to Launch
- `npm run dev:all` starts llama-server, the TTS API and the interface. Start the engine from the interface
- `npm run dev` starts only the interface
- Or double-click the launchers in `tools/`: `_run_llama.cmd`, `_run_tts_api.cmd`, then `_run_script.cmd`

`npm test` runs the curator tests.

---
# Machine Specs used:

### Main PC
- RTX 3090
- Ryzen 5 5600
- 32GB DDR4 3600MHz CL18
- Win11
- Cuda 12.4
- Python 3.12

### RAG PC
Running on the UGREEN NASync DXP4800
- 8GB DDR5
- Intel N100 Quad-Core
- 1TB NVME
> Hosting qdrant on main PC ended up having issues with HTTP timeout.

---

Additional Packages:
- ffmpeg

---
Dependencies:

- https://github.com/OpenBMB/MiniCPM-o
- https://huggingface.co/cognitivecomputations/dolphin-2.9-llama3-8b
- https://github.com/KoljaB/RealtimeTTS
- https://github.com/KoljaB/RealtimeSTT
---


# Modules

### pykernel
API responsible for rebooting module services when requested.
