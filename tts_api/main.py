import signal
from contextlib import asynccontextmanager
from fastapi import FastAPI
import sys
from dependencies import cprint, log
from engine import engine
import uvicorn
import torch
#  https://github.com/KoljaB/RealtimeTTS/tree/master/example_fast_api

@asynccontextmanager
async def lifespan(app: FastAPI):
    cprint("blue", "API started.")
    log("Debugs enabled.")

    engine.load_engine()

    # char_iterator = iter("Audio Loaded")
    # engine.engine_stream.feed(char_iterator)
    # engine.engine_stream.play()
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

def check_cuda():
    """Simply display whether CUDA is available and print device information.
    CoquiEngine will automatically use CUDA if available."""

    if torch.cuda.is_available():
        print("CUDA available.")
        print(f"Current: {torch.cuda.current_device()} - Count: {torch.cuda.device_count()}")
        print(f"Device Name: {torch.cuda.get_device_name(torch.cuda.current_device())}")
    

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
    check_cuda()
    signal.signal(signal.SIGINT, safe_exit)
    # app.run(port=8000)
    uvicorn.run("main:app", reload=True)