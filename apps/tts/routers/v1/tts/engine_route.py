from fastapi import APIRouter, Body, BackgroundTasks
from fastapi.responses import StreamingResponse
from dependencies import cprint
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

