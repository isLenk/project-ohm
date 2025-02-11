import signal
from RealtimeTTS import TextToAudioStream, CoquiEngine
from contextlib import asynccontextmanager
from typing import Union
from fastapi import FastAPI
from fastapi import Body
from fastapi import BackgroundTasks
from fastapi.responses import StreamingResponse
from queue import Queue
import threading
import sys
#  https://github.com/KoljaB/RealtimeTTS/tree/master/example_fast_api
def cprint(color="default", *args):
    def get_color(color="default"):
        match color:
            case "red":
                return '\033[31m'
            case "blue":
                return '\033[44m'
            case _:
                # gray
                return '\033[0m'
    print(get_color(color), *args, get_color())

DEBUG_LOGS_ENABLED = True

def log(*args, **kwargs):
    if not DEBUG_LOGS_ENABLED: return
    print("->", *args, **kwargs)

import wave
import io


play_tts_semaphore = threading.Semaphore(1)
tts_lock = threading.Lock()


class TTSEngine:
    stream: str
    engine_stream: TextToAudioStream
    audio_queue: Queue
    chunks_received: int

    def load_engine(self, model_name="xtts_v2", voice="voices/lance.wav", overwrite=False, *args, **kwargs):
        if hasattr(self, "engine") and overwrite == False and self.engine.model_name == model_name: 
            cprint("red", "Model already loaded.")
            return "Model already loaded."
        self.unload_engine()
        self.audio_queue = Queue()
        self.chunks_received = 0
        self.engine = CoquiEngine(model_name=model_name, voice=voice, *args, **kwargs)
        self.engine_stream = TextToAudioStream(self.engine, on_audio_stream_stop=self.on_audio_stream_stop)

        formatting, channel, sample_rate = self.engine.get_stream_info()
        cprint("blue", f"Model: {model_name}, Voice: {voice}")
        cprint("blue", f"Format: {formatting}, Channels: {channel}, Sample Rate: {sample_rate}")
        # Read the 
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
    
    def _on_audio_chunk(self, chunk):
        """Callback for handling audio chunks"""
        self.chunks_received += 1
        try:
            self.audio_queue.put(chunk)
        except Exception as e:
            cprint("red", e)
            
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

    # ? UNUSED ATM
    @_ensure_engine
    def feed_stream(self, url):
        """Designed for feeding a text streaming generation API"""
        log("Retrieved stream", url)
        for chunk in engine._openai_generator(url):
            print(chunk)
            engine.engine_stream.feed(chunk)
            engine.engine_stream.play()
    
    @_ensure_engine
    def feed_input(self, input, muted=True):
        """Play input text directly"""
        print(f"Feeding input: {input}")
        self.audio_queue = Queue()
        self.engine_stream.feed(input)
        self.engine_stream.play(muted=False,
                                on_audio_chunk=self._on_audio_chunk)
        # self.audio_queue.put(None)
        # self.audio_queue.put_nowait(None)

    @_ensure_engine
    def on_audio_stream_stop(self):
        """Callback for when the audio stream stops"""
        print("Audio stream stopped.")
        self.audio_queue.put(None)

    @_ensure_engine
    def finish_input(self):
        """Finish the input stream"""
        self.audio_queue.put(None)

    @_ensure_engine
    def stop_stream(self):
        """Cancels the current stream"""
        pass

    def audio_stream(self):
        """Generator for audio chunks"""
        pass

    def audio_chunk_generator(self, send_wave_headers=True):
        first_chunk = False
        try:
            while True:
                print(".", end="")
                chunk = self.audio_queue.get()
                if chunk is None:
                    print("Terminating stream")
                    break
                if not first_chunk:
                    if send_wave_headers:
                        print("Sending wave header")
                        yield create_wave_header_for_engine(self.engine)
                    first_chunk = True
                yield chunk
        except Exception as e:
            print(f"Error during streaming: {str(e)}")
        

def create_wave_header_for_engine(engine):
    _, _, sample_rate = engine.get_stream_info()

    num_channels = 1
    sample_width = 2
    frame_rate = sample_rate

    wav_header = io.BytesIO()
    with wave.open(wav_header, "wb") as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(frame_rate)

    wav_header.seek(0)
    wave_header_bytes = wav_header.read()
    wav_header.close()

    # Create a new BytesIO with the correct MIME type for Firefox
    final_wave_header = io.BytesIO()
    final_wave_header.write(wave_header_bytes)
    final_wave_header.seek(0)

    return final_wave_header.getvalue()

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

    char_iterator = iter("Audio Loaded")
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

@app.get("/api/v1/health")
async def get_health():
    import datetime
    # returns time in utc
    return {"status": "ok", "timestamp": datetime.datetime.now(datetime.timezone.utc)}

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

@app.post("/api/v1/tts/feed_stream")
async def post_feed_stream(url: str = Body(..., embed=True)):
    response = engine.feed_stream(url)
    return {"status": "success"}

@app.post("/api/v1/tts/stop_stream")
async def post_stop_stream():
    engine.stop_stream()
    return {"status": "success"}

@app.post("/api/v1/tts/finish_input")
async def post_finish_input():
    engine.finish_input()
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