This folder contains relevant Windows CMD files to execute the application.

### dev.mjs
Run through `npm run dev` / `npm run dev:all` from the repo root. Starts the interface, and with `all` also llama-server and the TTS API, prefixing each one's output.

### tts_api
Executes text to speech API

### run_script
Loads the python files and interface

### run_qdrant
Executes qdrant api for RAG

### run_llama
Executes llama.cpp server (Gemma 4 E4B) for text generation