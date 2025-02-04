from scipy.signal import resample
import numpy as np

def decode_and_resample(audio_data, original_sample_rate, target_sample_rate):
    try:
        audio_np = np.frombuffer(audio_data, dtype=np.int16)
        num_original_samples = len(audio_np)
        num_target_samples = int(num_original_samples * target_sample_rate / original_sample_rate)
        resampled_audio = resample(audio_np, num_target_samples)
        return resampled_audio.astype(np.int16).tobytes()
    except Exception as e:
        print(f"Error in resampling: {e}")
        return audio_data
