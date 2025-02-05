import requests
from urllib.parse import urlencode
import aiohttp
VOICE = "Morgan_Freeman CC3.wav"
ENDPOINT = "http://localhost:7851/api/tts-generate-streaming"

DOMAIN="127.0.0.1"
PORT=8000

class TTSModule:

    def __init__(self):
        self.voice = VOICE

    async def alltalk_tts_get_stream(self, text):
        print(f"Speaking: {text}")

        payload = {
            "text": text,
            "voice": self.voice,
            "language": "en",
            "output_file": "output.wav"
        }

        url = f"{ENDPOINT}?{urlencode(payload)}"

        return url
    
    def get_wav(self, text):
        response = self.get_stream(text)
        return response
    
    async def get_request_stream(self, text):
        url = f"http://{DOMAIN}:{PORT}/api/v1/tts/feed_input"
        data = {"input": text}
        try:
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, params={"stream": "true"}) as response:
                    first_chunk = True
                    async for chunk in response.content.iter_chunks():
                        yield chunk

                    # response.content.rea
                    # yield await response.content.()

                print("End of Stream.")
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
