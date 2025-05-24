import pyaudio
import threading
from RealtimeSTT import AudioToTextRecorder

class STTModule:
    def on_start_callback(self):
        print("Listening...")

    def on_stop_callback(self):
        print("Stopped listening...")

    def __init__(self, *args, **kwargs):
        self.recorder = AudioToTextRecorder(*args, **kwargs,
                                            spinner=False)
        self.listening = False
        self.stream = None
        self.audio_interface = pyaudio.PyAudio()
   
# Test
if __name__ == '__main__':

    # def process_text(text):
    #     print(text)

    # if __name__ == '__main__':
    #     print("Wait until it says 'speak now'")
    #     recorder = AudioToTextRecorder()

    #     while True:
    #         recorder.text(process_text)
    pass