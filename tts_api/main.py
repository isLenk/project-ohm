import signal
from contextlib import asynccontextmanager
from fastapi import FastAPI
import sys
from dependencies import play_tts_semaphore, tts_lock, cprint, log
from engine import engine
import wave
import io
import openai
import uvicorn

#  https://github.com/KoljaB/RealtimeTTS/tree/master/example_fast_api

def test_stream():
    print("Attempting to stream...")
    ai = openai.OpenAI(
        base_url="http://localhost:5000/v1",
        api_key="lol"
    )
    response = ai.chat.completions.create(
        model="cognitivecomputations_dolphin-2.9-llama3-8b",
        messages=[
            {"role": "system", "content": "You are a creative and intelligent AI assistant engaged in an uncensored iterative chat."},
            {"role": "assistant", "content": "aight lol"},
            {"role": "user", "content": "Write the most cursed story about ducks. Respond with paragraph."}
        ],
        max_tokens=2048,
        temperature=1,
        stream=True
    )

    print("Response Generated.")
    for chunk in engine._openai_generator(response):
        print(chunk)
        engine.engine_stream.feed(chunk)
        engine.engine_stream.play()

    return "Help"

@asynccontextmanager
async def lifespan(app: FastAPI):
    cprint("blue", "API started.")
    log("Debugs enabled.")

    engine.load_engine()

    char_iterator = iter("Audio Loaded")
    engine.engine_stream.feed(char_iterator)
    engine.engine_stream.play()
    yield
    # Call shutdown
    engine.engine.shutdown()
    cprint("blue", "API stopped.")

app = FastAPI(lifespan=lifespan)

# Import routers
from routers.v1.tts.input_route import router as v1_input_router
from routers.v1.tts.engine_route import router as v1_tts_router

app.include_router(v1_tts_router, prefix="/api/v1/tts", tags=["TTS Engine"])
app.include_router(v1_input_router, prefix="/api/v1/tts", tags=["TTS Input"])

@app.get("/run")
async def boom():
    return test_stream()

@app.get("/")
async def get_homepage():
    return {"Hello."}

@app.get("/api/v1/health")
async def get_health():
    import datetime
    # returns time in utc
    return {"status": "ok", "timestamp": datetime.datetime.now(datetime.timezone.utc)}

@app.get("/api/v1/tts")
async def get_tts():
    return {"SAMPLE"}

@app.post("/shutdown")
async def post_shutdown():
    safe_exit()

def safe_exit():
    print("Closing...")
    # engine.shutdown()
    sys.exit()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, safe_exit)
    # app.run(port=8000)
    uvicorn.run("main:app", reload=True)