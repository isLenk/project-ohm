import requests
from urllib.parse import urlencode
VOICE = "Morgan_Freeman CC3.wav"
ENDPOINT = "http://localhost:7851/api/tts-generate-streaming"

DOMAIN="localhost"
PORT=8000

import aiohttp

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

    async def send_request(self, text):
        """Send a request to the TTS server."""
        url = f"http://{DOMAIN}:{PORT}/api/v1/tts/feed_input"
        data = {"input": text}
        async with aiohttp.ClientSession() as session:
            # HACK https://stackoverflow.com/questions/27021440/python-requests-dont-wait-for-request-to-finish
            try:
                async with session.post(url, json=data, timeout=0.000001) as response:
                    return await response.json()
            except:
                pass