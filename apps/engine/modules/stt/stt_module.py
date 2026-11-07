# import pyaudio
# import threading
# from RealtimeSTT import AudioToTextRecorder
from faster_whisper import WhisperModel
import pyaudio
import numpy as np
# class STTModule:
#     def on_start_callback(self):
#         print("Listening...")

#     def on_stop_callback(self):
#         print("Stopped listening...")

#     def __init__(self, *args, **kwargs):
#         self.recorder = AudioToTextRecorder(*args, **kwargs,
#                                             spinner=False)
#         self.listening = False
#         self.stream = None
#         self.audio_interface = pyaudio.PyAudio()
   
# Test
if __name__ == '__main__':
    model_size = "large-v3"
    
    # Audio recording parameters
    RATE = 16000
    CHUNK = 1024
    p = pyaudio.PyAudio()
    print("----------------------record device list---------------------")
    info = p.get_host_api_info_by_index(0)
    numdevices = info.get('deviceCount')
    for i in range(0, numdevices):
            if (p.get_device_info_by_host_api_device_index(0, i).get('maxInputChannels')) > 0:
                print("Input Device id ", i, " - ", p.get_device_info_by_host_api_device_index(0, i).get('name'))

    print("-------------------------------------------------------------")

    stream = p.open(format=pyaudio.paInt16,
                    channels=1,
                    rate=RATE,
                    input=True,
                    input_device_index=1,  # Set to None to use the default input device
                    frames_per_buffer=CHUNK)

    print("Listening from microphone... Press Ctrl+C to stop.")

    model = WhisperModel(model_size, device="cuda", compute_type="int8_float16")

    try:
        while True:
            audio_data = b""
            for _ in range(0, int(RATE / CHUNK * 0.5)):  # Adjust the duration as needed
                audio_data += stream.read(CHUNK, exception_on_overflow=True)
            audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            segments, info = model.transcribe(audio_np, beam_size=5, language="en", task="transcribe")
            for segment in segments:
                print("[%.2fs -> %.2fs] %s" % (segment.start, segment.end, segment.text))
    except KeyboardInterrupt:
        print("Stopped listening.")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()
