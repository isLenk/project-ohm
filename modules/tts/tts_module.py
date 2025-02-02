import requests
from urllib.parse import urlencode

VOICE = "David_Attenborough CC3.wav"
ENDPOINT = "http://localhost:7851/api/tts-generate-streaming"

class TTSModule:
    def __init__(self):
        self.voice = VOICE

    def get_stream(self, text):
        print(f"Speaking: {text}")

        payload = {
            "text": text,
            "voice": self.voice,
            "language": "en",
            "output_file": "output.wav"
        }

        # Send x-www-form-urlencoded request
        # response = requests.request("POST", ENDPOINT, 
        #                             data=urlencode(payload),
        #                           headers={"Content-Type": "application/x-www-form-urlencoded"}, 
        #                           stream=True)
        # URL Encode the payload, make a GET request
        response = requests.get(f"{ENDPOINT}?{urlencode(payload)}", stream=True)
        return response
    
    def get_wav(self, text):
        response = self.get_stream(text)
        return response