import requests
from urllib.parse import urlencode
VOICE = "Morgan_Freeman CC3.wav"
ENDPOINT = "http://localhost:7851/api/tts-generate-streaming"

class TTSModule:
    def __init__(self):
        self.voice = VOICE

    async def get_stream(self, text):
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

# def get_stream(text):
#     payload = {
#         "text": text,
#         "voice": "Morgan_Freeman CC3.wav",
#         "language": "en",
#         "output_file": "output.wav"
#     }
#     url = f"http://localhost:7851/api/tts-generate-streaming?{urlencode(payload)}"
#     return url