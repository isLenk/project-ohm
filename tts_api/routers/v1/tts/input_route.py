from fastapi import APIRouter, Body, BackgroundTasks
from fastapi.responses import StreamingResponse
from dependencies import play_tts_semaphore, tts_lock
from engine import engine
import threading

router = APIRouter(
    tags=["TTS"],
    responses={404: {"description": "Not found"}},
)

@router.post("/feed_input")
async def post_feed_input(input: str = Body(..., embed=True), muted: bool = True, stream: bool = False):
    with tts_lock:
        if play_tts_semaphore.acquire(blocking=False):
            try:
                threading.Thread(target=engine.feed_input, args=(input, muted), daemon=True).start()
            finally:
                play_tts_semaphore.release()

        if stream:
            print("Streaming response")
            return StreamingResponse(engine.audio_chunk_generator(), media_type="audio/wav")
        return {"status": "success"}
    
@router.post("/feed_stream")
async def post_feed_stream(url: str = Body(..., embed=True)):
    response = engine.feed_stream(url)
    return {"status": "success"}

@router.post("/stop_stream")
async def post_stop_stream():
    engine.stop_stream()
    return {"status": "success"}

@router.post("/finish_input")
async def post_finish_input():
    engine.finish_input()
    return {"status": "success"}


@router.post("/feed_input")
async def post_feed_input(input: str = Body(..., embed=True), muted: bool = True, stream: bool = False):
    with tts_lock:
        if play_tts_semaphore.acquire(blocking=False):
            try:
                threading.Thread(target=engine.feed_input, args=(input, muted), daemon=True).start()
            finally:
                play_tts_semaphore.release()

        if stream:
            print("Streaming response")
            return StreamingResponse(engine.audio_chunk_generator(), media_type="audio/wav")
        return {"status": "success"}
