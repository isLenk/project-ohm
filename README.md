### Virtual Assistant
Project heavily inspired by Vedals Neuro.

Virtual Assistant is a passion project that can combines Text-To-Speech, Speech-To-Text, and Generative AI to produce human-like AI chatbots.

The project uses three models.
1. GenAI
2. TTS
3. MLLM for Computer Vision

### Features:
- Discord Integration
- Text-To-Speech: RealtimeTTS
- Voice-To-Text: FasterWhisper / Whisper on Discord
- Modularization: Toggleable abilities
- Vision: miniCPM

---
# Machine Specs used:

### Main PC
- RTX 3090
- Ryzen 5 5600
- 32GB DDR4 3600MHz CL18
- Win11
- Cuda 12.1 (Last available CUDA versions compatible with DeepSpeed that I could find)
- Python 3.12

### RAG PC
Running on the UGREEN NASync DXP4800
- 8GB DDR5
- Intel N100 Quad-Core
- 1TB NVME
> Hosting qdrant on main PC ended up having issues with HTTP timeout.

---

Additional Packages:
- ffmpeg
