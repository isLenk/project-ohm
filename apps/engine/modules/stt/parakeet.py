# Parakeet STT + Silero VAD, loaded once and shared.
# ? Kept out of modules/discord so dev.py can hot reload the bot without reloading ~2.5GB of models
from huggingface_hub import hf_hub_download
import onnxruntime as ort
ort.preload_dlls()
ort.set_default_logger_severity(3) # Hide the "nodes not assigned" warnings
import onnx_asr

_model = None
_vad = None
def get_models():
    global _model, _vad
    if _model is None:
        # ? pad_to_nc1d: cuDNN picks a very slow algorithm for the encoder's 1D convs without it
        # (encoder ~140ms -> ~40ms per utterance on a 3090, same transcripts)
        _model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", providers=[
            ("CUDAExecutionProvider", {"cudnn_conv1d_pad_to_nc1d": "1"}),
        ])
        _vad = ort.InferenceSession(hf_hub_download("istupakov/silero-vad-onnx", "silero_vad.onnx"), providers=["CPUExecutionProvider"])
    return _model, _vad
