import requests
from urllib.parse import urlencode
import aiohttp
import asyncio
import io
import websockets
import modules.discord.utils.DiscordVoiceUtil as DC_Util


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
        for chunk in response:
            if chunk.choices[0].delta.content is None:
                break
            if ((num_chunks := num_chunks + 1) % 15) == 0:
                yield
                print("F", end="")
            print(chunk.choices[0].delta.content, end="")
            # Feed the chunk to the websocket
            await websocket.send(chunk.choices[0].delta.content)

        await websocket.send("END")

    async def receive_from_websocket(self, 
                                     websocket, 
                                     fn_push, 
                                     until_done=False,
                                     sample_rate=24000,
                                     min_buffer_scale=5,
                                     chunks_per_block=100):
        """Receive audio chunks from the websocket."""
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

    async def ws_thread_manager(self, websocket, response, fn_push):
        """Feeds the response to the websocket and receives audio chunks. Closes the websocket when done."""
        try:
            async for _ in self.feed_to_websocket(websocket, response):
                await self.receive_from_websocket(websocket, fn_push)
            await self.receive_from_websocket(websocket, fn_push, until_done=True)
        except websockets.exceptions.ConnectionClosedError:
            print("Connection closed")
        except Exception as e:
            print(e)
        finally:
            await websocket.close()
        