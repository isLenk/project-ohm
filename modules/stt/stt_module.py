import pyaudio
import threading
from RealtimeSTT import AudioToTextRecorder

class STTModule:
    def __init__(self):
        self.audioToTextRecorder = AudioToTextRecorder()
        self.listening = False
        self.stream = None
        self.audio_interface = pyaudio.PyAudio()

    def start_listening(self):
        self.listening = True
        self.stream = self.audio_interface.open(format=pyaudio.paInt16,
                                                channels=1,
                                                rate=16000,
                                                input=True,
                                                frames_per_buffer=1024)
        threading.Thread(target=self._listen).start()

    def _listen(self):
        while self.listening:
            data = self.stream.read(1024)
            self.audioToTextRecorder.record(data)

    def stop_listening(self):
        self.listening = False
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
        self.audio_interface.terminate()

    def shutdown(self):
        self.stop_listening()
        self.audioToTextRecorder.shutdown()

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