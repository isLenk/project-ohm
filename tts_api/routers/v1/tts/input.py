from fastapi import APIRouter, Body, BackgroundTasks
from fastapi.responses import StreamingResponse

# router = APIRouter()

@app.post("/api/v1/tts/feed_input")
async def post_feed_input(background_tasks: BackgroundTasks, input: str = Body(..., embed=True), muted: bool = True, stream: bool = False):
    # background_tasks.add_task(engine.feed_input, input, muted)
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