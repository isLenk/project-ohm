import signal
from RealtimeTTS import TextToAudioStream, CoquiEngine
from contextlib import asynccontextmanager
from typing import Union
from fastapi import FastAPI
from fastapi import Body
from fastapi import BackgroundTasks
import sys

def cprint(color="default", *args):
    def get_color(color="default"):
        match color:
            case "red":
                return '\033[31m'
            case "blue":
                return '\033[44m'
            case _:
                return '\033[0m'
    print(get_color(color), *args, get_color())

DEBUG_LOGS_ENABLED = True

def log(*args, **kwargs):
    if not DEBUG_LOGS_ENABLED: return
    print("->", *args, **kwargs)

class TTSEngine:
    stream: str
    engine_stream: TextToAudioStream

    def load_engine(self, model_name="xtts_v2", voice="voices/lance.wav", overwrite=False, *args, **kwargs):
        if hasattr(self, "engine") and overwrite == False and self.engine.model_name == model_name: 
            cprint("red", "Model already loaded.")
            return "Model already loaded."
        self.unload_engine()
        self.engine = CoquiEngine(model_name=model_name, voice=voice, *args, **kwargs)
        self.engine_stream = TextToAudioStream(self.engine)
        cprint("blue", "Model Loaded")

    def unload_engine(self):
        if not hasattr(self, "engine"):
            cprint("red", "No engine to unload.")
            return
        try:
            self.engine_stream.stop()
            self.engine.shutdown()
        except Exception:
            pass
    
    def _ensure_engine(func):
        """Decorator to ensure engine is loaded before running a function"""
        def wrapper(self, *args, **kwargs):
            if not hasattr(self, "engine"):
                cprint("red", "No engine loaded.")
                return "No engine loaded."
            return func(self, *args, **kwargs)
        return wrapper
    
    @staticmethod
    def _openai_generator(gen_stream):
        """Generator for OpenAI streaming API.
        Yields sentences as they are completed."""
        payload = ""
        log("Starting generator...")
        for chunk in gen_stream:
            if (content := chunk.choices[0].delta.content) is not None:
                print(content, end="-")
                ends = ["?", ".", "!"]
                results = [end in content for end in ends]
                if any(results):
                    payload += content
                    # Get index of last found end in content
                    last = max([payload.rindex(ends[i]) for i, x in enumerate(results) if x])
                    feed, payload = payload[:last+1], payload[last+1:]
                        
                    yield feed
                else:
                    payload += content

        if payload.strip() != "":
            yield payload

        log("Generator finished.")

    @_ensure_engine
    def feed_stream(self, url):
        """Designed for feeding a text streaming generation API"""
        log("Retrieved stream", url)
        for chunk in engine._openai_generator(url):
            print(chunk)
            engine.engine_stream.feed(chunk)
            engine.engine_stream.play()
    
    @_ensure_engine
    def feed_input(self, input):
        """Play input text directly"""
        self.engine_stream.feed(input)
        self.engine_stream.play()

    @_ensure_engine
    def stop_stream(self):
        """Cancels the current stream"""
        pass

import openai

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

    char_iterator = iter("Audio Test")
    engine.engine_stream.feed(char_iterator)
    engine.engine_stream.play()
    yield
    # Call shutdown
    engine.engine.shutdown()
    cprint("blue", "API stopped.")

app = FastAPI(lifespan=lifespan)
engine = TTSEngine()

@app.get("/run")
async def boom():
    return test_stream()

@app.get("/")
async def get_homepage():
    return {"Hello."}

@app.get("/api/v1/tts")
async def get_tts():
    return {"SAMPLE"}


@app.post("/api/v1/tts/change_engine")
async def post_tts( model_name: str, voice: str):
    engine.load_engine(model_name=model_name, voice=voice)
    return {"status": "success"}

@app.post("/api/v1/tts/unload_engine")
async def post_unload_engine():
    engine.unload_engine()
    return {"status": "success"}

@app.post("/api/v1/tts/feed_input")
async def post_feed_input(background_tasks: BackgroundTasks, input: str = Body(..., embed=True)):
    background_tasks.add_task(engine.feed_input, input)
    # response = engine.feed_input(input)
    return {"status": "success"}

@app.post("/api/v1/tts/feed_stream")
async def post_feed_stream(url: str = Body(..., embed=True)):
    response = engine.feed_stream(url)
    return {"status": "success"}

@app.post("/api/v1/tts/stop_stream")
async def post_stop_stream():
    engine.stop_stream()
    return {"status": "success"}

@app.post("/shutdown")
async def post_shutdown():
    safe_exit()

@app.get("/start_engine")
async def get_start_engine():
    cprint("blue", "Starting engine...")
    engine.load_engine()
    return {"status": "success"}

def safe_exit():
    print("Closing...")
    # engine.shutdown()
    sys.exit()


if __name__ == "main":
    signal.signal(signal.SIGINT, safe_exit)

    app.run(port=8000)