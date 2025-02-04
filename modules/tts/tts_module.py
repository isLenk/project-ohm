import requests
from urllib.parse import urlencode
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

    def send_request(self, text):
        """Send a request to the TTS server."""
        url = f"http://{DOMAIN}:{PORT}/api/v1/tts/feed_input"
        data = {"input": text}
        try:
            requests.post(url, json=data)
        except requests.exceptions.ConnectionError:
            pass
