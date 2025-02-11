import requests
from urllib.parse import urlencode
import aiohttp
import asyncio
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

    async def send_request(self, text):
        """Send a request to the TTS server."""
        url = f"http://{DOMAIN}:{PORT}/api/v1/tts/feed_input"
        data = {"input": text}
        try:
            async with aiohttp.ClientSession() as session:
                return await session.post(url, json=data)
        except requests.exceptions.ConnectionError:
            pass

    async def finish_input(self):
        """Finish the input stream"""
        url = f"http://{DOMAIN}:{PORT}/api/v1/tts/finish_input"
        try:
            async with aiohttp.ClientSession() as session:
                return await session.post(url)
        except requests.exceptions.ConnectionError:
            pass