import requests
from urllib.parse import urlencode
import aiohttp
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import websockets
import modules.discord.utils.DiscordVoiceUtil as DC_Util
import utils.latency as latency


VOICE = "Morgan_Freeman CC3.wav"
ENDPOINT = "http://localhost:7851/api/tts-generate-streaming"

DOMAIN="127.0.0.1"
PORT=8000

class TTSModule:

    def __init__(self):
        self.voice = VOICE

    async def get_request_stream(self, text):
        url = f"http://{DOMAIN}:{PORT}/api/v1/tts/feed_input"
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