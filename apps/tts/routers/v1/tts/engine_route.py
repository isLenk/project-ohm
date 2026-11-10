import asyncio
from fastapi import APIRouter, Body, BackgroundTasks, HTTPException, Query
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
    """The engine speaking and its voice"""
    return {"engine": engine.engine_name, "file": engine.voice_name}

@router.post("/voice")
async def post_voice(file: str = "", engine_name: str = Query("chatterbox", alias="engine")):
    """Speak as voices/<file> ("" for the built-in voice), or Kokoro's voice <file> with engine=kokoro.
    Loads it now so the next reply doesn't wait. Waits for a reply in progress first."""
    async with session_lock:
        try:
            await asyncio.to_thread(engine.set_voice, file, engine_name)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except AssertionError:
            raise HTTPException(400, f"{file} is too short. A voice clip needs more than 5 seconds of speech.")
        except RuntimeError as e:
            raise HTTPException(503, str(e))
    return {"engine": engine.engine_name, "file": engine.voice_name}
