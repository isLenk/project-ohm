import asyncio
from fastapi import APIRouter, Body, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from dependencies import cprint, session_lock
from engine import engine

router = APIRouter(
    tags=["TTS"],
    responses={404: {"description": "Not found"}},
)

@router.get("/start_engine")
async def get_start_engine():
    cprint("blue", "Starting engine...")
    engine.load_engine()
    return {"status": "success"}

@router.post("/unload_engine")
async def post_unload_engine():
    engine.unload_engine()
    return {"status": "success"}

@router.post("/change_engine")
async def post_tts( model_name: str, voice: str):
    engine.load_engine(model_name=model_name, voice=voice)
    return {"status": "success"}



@router.get("/voice")
async def get_voice():
    """The clip in voices/ being spoken as, "" for the built-in voice."""
    return {"file": engine.voice_name}

@router.post("/voice")
async def post_voice(file: str = ""):
    """Speak as voices/<file> from now on ("" for the built-in voice). Encodes the clip now, so the
    next reply doesn't wait on it. Waits for a reply in progress to finish first."""
    async with session_lock:
        try:
            await asyncio.to_thread(engine.set_voice, file)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except AssertionError:
            raise HTTPException(400, f"{file} is too short. A voice clip needs more than 5 seconds of speech.")
        except RuntimeError as e:
            raise HTTPException(503, str(e))
    return {"file": engine.voice_name}
