# DEVNOTES
Contained in this file is all of the troubles and start of tech debt that I've come across during development. Events prior to June 15 2025 have been loosely documented as that day was when this file was created.

The TTS and STT use [KoljaB's](https://github.com/KoljaB) RealtimeSTT/RealtimeTTS libraries to process audio. The libraries are then loaded in python served through a FastAPI service. Rather than loading all chunks prior to sending back a respoonse-- thus introducing delay-- the audio is instead fed through websockets.



### June 15 2025
- Due to time constraints, I've fixed character in `/api/:character/memories` to be Ohm. In other words, all characters share the same set of memories.
- For two reasons, namely time and efficiency, memory retrieval/generation is done in two parts. The python end (which I'd already implemented LLM handling in) will first send a memory retrieval request, then 
- Due to semantic search 

### June 16 2025
- Switched to Axios since node-fetch and electron-fetch were being unreasonably difficult to use without altering package.json to use ES6 (breaking loading files like preload)


### June 25 2025
I realized I had not enabled deepspeed on the TTS engine. Hence I fell into the rabbit hole of incompatibilities and lack of support for windows deepspeed. Although this won't be an issue down the road when the project is either hosted on docker containers or a separate linux-based system, I wanted to have it readily available on Windows. After copious amounts of searching and many CUDA kit instalations later, I finally had a working set of libraries... until it didn't work. It appears that the newest version of the Transformer library (`transformers-4.52.4`) removed `_validate_model_class` hence resulting in:
```
AttributeError: 'GPT2InferenceModel' object has no attribute '_validate_model_class'

Error: 'GPT2InferenceModel' object has no attribute '_validate_model_class'
```
Luckily, a quick search on the RealtimeTTS/issues brought up:
https://github.com/KoljaB/RealtimeTTS/issues/324
to which I just needed to downgrade the library.

I was finally able to successfully feed the input and receive a response again. However, that was without deepspeed enabled. Toggling it on, I receive a version mismatch between the compiled deepspeed library and torch. Luckily, they specified which exact version I needed (`2.1 -> 2.5`). Thus a quick re-install later and now I am able to run with deepspeed toggled.

https://github.com/deepspeedai/DeepSpeed/releases


```
py -3.11 venv tts_env
pip install deepspeed-0.15.2+cuda121-cp311-cp311-win_amd64.whl
pip uninstall torch torchvision torchaudio
pip install torch==2.5.0 torchvision==0.20.0 torchaudio==2.5.0 --index-url https://download.pytorch.org/whl/cu121
pip uninstall transformers
pip install transformers==4.48.1
```

### June 29 2025
- References https://github.com/SYSTRAN/faster-whisper/issues/1240 for basic venv install but not enough. Needed cuDNN 9.10.2 but that was straightforward (my disk space is dissapearing). Still not enough, having varying CUDA installs makes life a bit more painful.
- Resolved fortunately through: [https://github.com/SYSTRAN/faster-whisper/issues/1230 ](https://github.com/SYSTRAN/faster-whisper/issues/1230#issuecomment-2628614311). Running 11.8.