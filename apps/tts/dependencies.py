import threading
from fastapi import Depends
from typing import Annotated
import io
import wave
from queue import Queue

play_tts_semaphore = threading.Semaphore(1)
tts_lock = threading.Lock()

# Queue to store text to be converted to audio
text_queue = Queue()

# Chunk Queue stores audio chunks to be sent to the client
chunk_queue = Queue()

DEBUG_LOGS_ENABLED = True


async def common_params():
    return {"locks": {"tts": tts_lock, "play_tts": play_tts_semaphore}}

CommonDep = Annotated[dict, Depends(common_params)]

def cprint(color="default", *args):
    def get_color(color="default"):
        match color:
            case "red":
                return '\033[31m'
            case "blue":
                return '\033[44m'
            case _:
                # gray
                return '\033[0m'
    print(get_color(color), *args, get_color())

def log(*args, **kwargs):
    if not DEBUG_LOGS_ENABLED: return
    print("->", *args, **kwargs)

def create_wave_header_for_engine(engine):
    _, _, sample_rate = engine.get_stream_info()

    num_channels = 1
    sample_width = 2
    frame_rate = sample_rate

    wav_header = io.BytesIO()
    with wave.open(wav_header, "wb") as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(frame_rate)

    wav_header.seek(0)
    wave_header_bytes = wav_header.read()
    wav_header.close()

    # Create a new BytesIO with the correct MIME type for Firefox
    final_wave_header = io.BytesIO()
    final_wave_header.write(wave_header_bytes)
    final_wave_header.seek(0)

    return final_wave_header.getvalue()
