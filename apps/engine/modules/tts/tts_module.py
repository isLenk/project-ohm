import os
import random
import time
import requests
from dotenv import load_dotenv
from urllib.parse import urlencode
import aiohttp
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import websockets
import modules.discord.utils.DiscordVoiceUtil as DC_Util
import utils.latency as latency
from controller.memory import memory, voice_engine, voice_file


ENDPOINT = "http://localhost:7851/api/tts-generate-streaming"
VOICE_TTL = 30          # seconds before the character's voice is re-read from the NAS
PRELOAD_FOR = 180       # seconds to keep trying to reach a TTS server that's still loading its model
# Said while a reply waits on a web search. Pre-made audio: the TTS holds back a lone sentence
# ? ~1.5-2s each, long enough to cover most searches
SEARCH_FILLERS = ("one sec, lemme look.", "hold on, lemme check.", "lemme look that up real quick.",
                  "gimme a sec, lemme see.")
FILLER_TIMEOUT = 30     # seconds per filler clip
TAGS = {"chatterbox": True, "kokoro": False}  # engines that can do [sigh] etc.

load_dotenv()
# tts_api/ (set TTS_URL in Settings when it runs somewhere else)
TTS_URL = os.getenv("TTS_URL", "http://127.0.0.1:8000").rstrip("/")
WS_URL = TTS_URL.replace("http", "ws", 1) + "/api/v1/tts/ws"

class TTSModule:

    def __init__(self, model=None):
        """model: the character in play (ModelObject). Its voice is sent with every reply, so the TTS
        server speaks as them even after it restarts or another character used it."""
        self.slug = getattr(model, "slug", None)
        self.name = getattr(model, "name", None) or self.slug
        self._voice = getattr(model, "voice", None)  # clip or Kokoro preset, None = default
        self._engine = getattr(model, "voice_engine", None) or "chatterbox"
        self._voice_at = time.monotonic()
        self._fillers = (None, [])  # ((engine, voice), [PCM])
        self._last_filler = None
        self._making_fillers = False

    def voice(self) -> str:
        """The character's voice ("" for the default), re-read with the engine from the NAS every VOICE_TTL. Blocking."""
        if self.slug and time.monotonic() - self._voice_at >= VOICE_TTL:
            self._voice_at = time.monotonic()
            row = memory.get_character(self.slug)
            if row:  # NAS down or character gone: keep what we had
                voice, engine = voice_file(row), voice_engine(row)
                if (voice, engine) != (self._voice, self._engine):
                    print(f"[tts] {self.name}'s voice changed on the Characters page: {engine}, {voice or 'default'}")
                self._voice, self._engine = voice, engine
        return self._voice or ""

    def engine(self) -> str:
        """As of the last voice()"""
        return self._engine

    def tags(self) -> bool:
        """Whether the engine can do [sigh] etc."""
        return TAGS.get(self._engine, True)

    def _url(self, voice: str, engine: str) -> str:
        return f"{WS_URL}?{urlencode({'voice': voice, 'engine': engine})}"

    def ws_url(self) -> str:
        """The TTS websocket for one reply, in the character's voice. Blocking (see voice())."""
        voice = self.voice()  # ? first: it can change the engine
        return self._url(voice, self._engine)

    async def preload_voice(self):
        """Have the TTS server encode the character's voice now (~1s), so their first reply doesn't
        wait on it. The app starts the TTS server alongside the engine, so keep trying while it loads."""
        voice = await asyncio.to_thread(self.voice)
        engine = self._engine
        deadline = time.monotonic() + PRELOAD_FOR
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            while True:
                try:
                    params = {"file": voice, "engine": engine}
                    async with session.post(f"{TTS_URL}/api/v1/tts/voice", params=params) as r:
                        if r.ok:
                            print(f"[tts] Speaking as {self.name} ({engine}, {voice or 'default voice'})")
                        else:
                            detail = (await r.json(content_type=None) or {}).get("detail", r.reason)
                            print(f"[tts] Couldn't load {self.name}'s {engine} voice {voice}: {detail}. "
                                  "Replies use its default voice.")
                    break
                except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
                    if time.monotonic() >= deadline:
                        print(f"[tts] TTS server isn't up at {TTS_URL}; {self.name}'s voice loads with their first reply.")
                        return
                await asyncio.sleep(3)
        await self.prepare_fillers()

    async def prepare_fillers(self):
        """Make the SEARCH_FILLERS clips in the current voice"""
        if self._making_fillers:
            return
        self._making_fillers = True
        try:
            voice = await asyncio.to_thread(self.voice)
            engine = self._engine
            clips = []
            for line in SEARCH_FILLERS:
                try:
                    clip = await asyncio.wait_for(self._synthesize(line, voice, engine), FILLER_TIMEOUT)
                except Exception as e:
                    print(f"[tts] Couldn't make the filler line {line!r}: {e!r}")
                    continue
                if clip:
                    clips.append(clip)
            self._fillers = ((engine, voice), clips)
            if clips:
                print(f"[tts] {len(clips)} filler lines ready for web searches")
        finally:
            self._making_fillers = False

    async def _synthesize(self, text: str, voice: str, engine: str = "chatterbox") -> bytes:
        """`text` as raw PCM"""
        audio = bytearray()
        async with websockets.connect(self._url(voice, engine), max_size=None) as ws:
            await ws.send(text)
            await ws.send("END")
            while (message := await ws.recv()) != "END":
                if isinstance(message, bytes):
                    audio += message
        return bytes(audio)

    def filler(self) -> bytes | None:
        """A random filler clip, never the same twice in a row; None until made for this voice"""
        clips = self._fillers[1]
        if self.fillers_stale() or not clips:
            return None
        options = [c for c in clips if c is not self._last_filler] or clips
        self._last_filler = random.choice(options)
        return self._last_filler

    def fillers_stale(self) -> bool:
        """The fillers are in an old voice or engine"""
        made_in = self._fillers[0]
        return made_in is not None and made_in != (self._engine, self._voice or "")

    async def get_request_stream(self, text):
        url = f"{TTS_URL}/api/v1/tts/feed_input"
        data = {"input": text}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, params={"stream": "true"}) as response:
                    async for chunk in response.content.iter_any():
                        yield chunk

        except requests.exceptions.ConnectionError:
            print("Crash!")

    async def feed_to_websocket(self, websocket, response):
        """Feed the response to the websocket."""
        num_chunks = 0
        # ? The LLM stream is a sync iterator; pull from it on a worker thread so waiting
        # for tokens doesn't freeze the event loop (and the audio receive loop with it)
        # ? One thread per reply so close() below always runs after any next() still in flight
        # (closing a generator another thread is inside of raises "generator already executing")
        llm_thread = ThreadPoolExecutor(max_workers=1)
        iterator = iter(response)
        _done = object()
        try:
            while (chunk := await asyncio.wrap_future(llm_thread.submit(next, iterator, _done))) is not _done:
                # ? Some chunks have no text (role/reasoning/usage), skip them instead of stopping
                if not chunk.choices or not chunk.choices[0].delta.content:
                    continue
                latency.mark("llm_first_token")
                if ((num_chunks := num_chunks + 1) % 15) == 0:
                    yield
                    print("F", end="")
                print(chunk.choices[0].delta.content, end="")
                # Feed the chunk to the websocket
                await websocket.send(chunk.choices[0].delta.content)

            await websocket.send("END")
        finally:
            # Interrupted or done: close the stream so the LLM stops generating
            if hasattr(response, "close"):
                llm_thread.submit(response.close)
            llm_thread.shutdown(wait=False)

    websocket = None

    async def receive_from_websocket(self, 
                                     websocket, 
                                     fn_push, 
                                     until_done=False,
                                     sample_rate=24000,
                                     min_buffer_scale=5,
                                     chunks_per_block=100,
                                     raw=False):
        """Receive audio chunks from the websocket.
        raw=True hands every chunk straight to fn_push(bytes) with no buffering."""
        if raw:
            try:
                while True:
                    message = await websocket.recv()
                    if message == "END":
                        break
                    await fn_push(message)
            except websockets.exceptions.ConnectionClosed:
                print("Connection closed")
            except Exception as e:
                print(e)
            return

        out_buffer = io.BytesIO()
        min_buffer_size = sample_rate * min_buffer_scale
        try:
            counter = 0
            while (counter := counter + 1) % chunks_per_block != 0 or until_done:
                # Message is either bytes or text "END"
                message = await websocket.recv()
                if message == "END":
                    break
                out_buffer.write(message)
                if out_buffer.tell() >= min_buffer_size:
                    out_buffer = await fn_push(out_buffer)
                
            if out_buffer.tell() > 0:
                await fn_push(out_buffer)
        except websockets.exceptions.ConnectionClosedError:
            print("Connection closed")
        except Exception as e:
            print(e)

    async def ws_thread_manager(self, websocket, response, fn_push, raw=False):
        """Feeds the response to the websocket and receives audio chunks. Closes the websocket when done."""
        feed_task = None
        try:
            self.websocket = websocket

            async def feed():
                try:
                    async for _ in self.feed_to_websocket(websocket, response): pass
                except Exception as e:
                    # ? Otherwise the receive loop waits forever for an END that never comes
                    print(f"Feeding TTS failed: {e!r}")
                    await websocket.close()

            # ? Feed text and receive audio at the same time. Taking turns deadlocks with Chatterbox
            # since it sends one big chunk per sentence instead of lots of small ones
            feed_task = asyncio.create_task(feed())
            await self.receive_from_websocket(websocket, fn_push, until_done=True, raw=raw)
            await feed_task
        except websockets.exceptions.ConnectionClosedError:
            print("Connection closed")
        except Exception as e:
            print(e)
        finally:
            # Interrupted (this task was cancelled): stop pulling text from the LLM too
            if feed_task and not feed_task.done():
                feed_task.cancel()
            self.websocket = None
            await websocket.close()

    async def interrupt(self):
        """Interrupt the websocket connection."""
        if self.websocket:
            await self.websocket.close()
        else:
            print("No websocket to interrupt")