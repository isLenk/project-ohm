@echo off
@REM Creates .venv with the engine's and curator's dependencies (see pyproject.toml).
@REM Needs uv: https://docs.astral.sh/uv/  (winget install astral-sh.uv)
@REM The TTS API has its own venv, see apps\tts\README.md.

uv sync --all-packages
