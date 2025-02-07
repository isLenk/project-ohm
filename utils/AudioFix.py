from scipy.signal import resample
import numpy as np
import wave

def decode_and_resample(audio_data, original_sample_rate, target_sample_rate) -> bytes:
    try:
        audio_np = np.frombuffer(audio_data, dtype=np.int16)
        num_original_samples = len(audio_np)
        num_target_samples = int(num_original_samples * target_sample_rate / original_sample_rate)
        resampled_audio = resample(audio_np, num_target_samples)
        return resampled_audio.astype(np.int16).tobytes()
    except Exception as e:
        print(f"Error in resampling: {e}")
        return audio_data

def fill_wav_buffer(buffer, 
                    chunks,
                    num_channels=1,
                    sample_rate=22050,
                    sample_width=2) -> None:
    with wave.open(buffer, "wb") as buf:
        buf.setnchannels(num_channels)
        buf.setsampwidth(sample_width)
        buf.setframerate(sample_rate)
        buf.writeframes(chunks)
    buffer.seek(0)
    return buffer