"""Times a TTS engine on Ohm's replies (first audio, RTF) and saves a wav per line to out/. score.py rates them.
    python bench_tts.py <chatterbox|pocket|kokoro|qwen06|qwen17> <voice> <idle|llm>
? Own venv (py3.12, torch cu128): its torch/transformers clash with tts_api's. llm = llama-server busy on :5077."""
import json, os, re, sys, threading, time, asyncio
from pathlib import Path
import numpy as np, soundfile as sf
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from spoken import spoken                      # same clean-up as the server
engine, voice, condition = sys.argv[1:4]
REF, REF_TEXT = str(HERE / "out" / "lance_ref.wav"), ("hello this is a sentence and i'm saying some random information for example "
    "i dislike talking to human beings because i find them quite annoying at times i like candy bars and twix and chocolates")
# Real replies; only Chatterbox gets the [tags]
LINES = [
    "[groan] okay, fine. you're in canada. that sign is a huge giveaway lol",
    "it's ontario. specifically looking like northern ontario based on those lakes and that forest.",
    "take your time. no rush at all.",
    "yeah? go on.",
    "[sigh] way further than bay area. you're looking at like 20+ hours of driving if you start from the south.",
    "my bad. i definitely missed the nova scotia curve there. looks like we had to pivot fast lol",
    "[chuckle] it's 2 to 1 in favor of T1 right now. they're definitely cooking.",
    "honestly, i'm not 100% sure on the exact town, but it feels like a solid southern vibe. maybe around tuscany or something similar.",
    "rue de clichy. sounds way more fancy than most streets i know.",
    "[laugh] fair enough. i'm down to wherever your brain decides is the vibe today.",
]
def text_for(line):
    return line if engine == "chatterbox" else re.sub(r"\[[^\]]*\]\s*", "", line)

# ── the LLM busy on the same GPU ──
stop = threading.Event()
def keep_llm_busy():
    import requests
    while not stop.is_set():
        try:
            with requests.post("http://127.0.0.1:5077/v1/chat/completions", stream=True, timeout=60, json={
                    "messages": [{"role": "user", "content": "Tell me a long story about a dragon."}],
                    "max_tokens": 300, "stream": True}) as r:
                for _ in r.iter_lines():
                    if stop.is_set(): break
        except Exception as e:
            print("llm load failed:", e); time.sleep(1)

# ── engines: text -> float32 chunks ──
def make():
    if engine == "pocket":
        from pocket_tts import TTSModel
        m = TTSModel.load_model()
        state = m.get_state_for_audio_prompt(REF if voice == "lance" else voice, truncate=True)
        def run(t):
            for c in m.generate_audio_stream(state, t):
                yield c.detach().float().cpu().numpy().reshape(-1)
        return run, m.sample_rate
    if engine == "kokoro":
        from kokoro import KPipeline
        p = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device="cuda")
        def run(t):
            for _, _, audio in p(t, voice=voice, speed=1.0):
                yield audio.detach().float().cpu().numpy().reshape(-1)
        return run, 24000
    if engine.startswith("qwen"):
        import torch
        from qwen_tts import Qwen3TTSModel
        name = {"qwen06": "Qwen/Qwen3-TTS-12Hz-0.6B-Base", "qwen17": "Qwen/Qwen3-TTS-12Hz-1.7B-Base"}[engine]
        m = Qwen3TTSModel.from_pretrained(name, device_map="cuda:0", dtype=torch.bfloat16)
        prompt = m.create_voice_clone_prompt(ref_audio=REF, ref_text=REF_TEXT)
        def run(t):
            wavs, run.rate = m.generate_voice_clone(text=t, language="English", voice_clone_prompt=prompt)
            yield np.asarray(wavs[0], dtype=np.float32).reshape(-1)
        return run, None
    if engine == "chatterbox":
        import websockets
        from urllib.parse import urlencode
        url = f"ws://127.0.0.1:8077/api/v1/tts/ws?{urlencode({'voice': '' if voice == 'builtin' else 'lance.wav'})}"
        def run(t):
            q = []
            started = time.perf_counter(); arrivals = []
            async def timed():
                async with websockets.connect(url, max_size=None) as ws:
                    await ws.send(t); await ws.send("END")
                    while (m := await ws.recv()) != "END":
                        if isinstance(m, bytes):
                            arrivals.append(time.perf_counter() - started)
                            q.append(np.frombuffer(m, dtype=np.int16).astype(np.float32) / 32768)
            asyncio.run(timed())
            run.arrivals = arrivals
            yield from q
        return run, 24000
    raise SystemExit(f"unknown engine {engine}")

def reference():
    """Start of voices/lance.wav at 24 kHz, for cloning"""
    if os.path.exists(REF) or voice != "lance":
        return
    import librosa
    a, rate = sf.read(HERE.parent / "voices" / "lance.wav")
    a = a.mean(1) if a.ndim > 1 else a
    os.makedirs(os.path.dirname(REF), exist_ok=True)
    sf.write(REF, librosa.resample(a[: int(13.4 * rate)].astype(np.float32), orig_sr=rate, target_sr=24000), 24000)


reference()
run, sr = make()
if condition == "llm":
    threading.Thread(target=keep_llm_busy, daemon=True).start(); time.sleep(2)
for _ in run(spoken(text_for("warm up, just getting the voice ready."))): pass

out = HERE / "out" / f"{engine}-{voice}-{condition}"; out.mkdir(parents=True, exist_ok=True)
rows = []
for i, line in enumerate(LINES):
    t = spoken(text_for(line)); started = time.perf_counter(); first = None; chunks = []
    for c in run(t):
        if first is None: first = time.perf_counter() - started
        chunks.append(c)
    total = time.perf_counter() - started
    if engine == "chatterbox": first = run.arrivals[0] if run.arrivals else total
    audio = np.concatenate(chunks) if chunks else np.zeros(1, np.float32)
    rate = sr or run.rate  # qwen tells its rate per call
    sf.write(out / f"{i:02d}.wav", audio, rate)
    rows.append({"i": i, "text": re.sub(r"\[[^\]]*\]\s*", "", line), "spoken": t, "ttfa": first, "total": total,
                 "audio_s": len(audio) / rate})
stop.set()
ttfa = sorted(r["ttfa"] for r in rows); rtf = sorted(r["total"] / r["audio_s"] for r in rows)
summary = {"engine": engine, "voice": voice, "condition": condition, "ttfa_med": ttfa[len(ttfa) // 2], "ttfa_max": ttfa[-1],
           "rtf_med": rtf[len(rtf) // 2], "rows": rows}
try:
    import torch; summary["vram_gb"] = torch.cuda.max_memory_allocated() / 1e9
except Exception: pass
json.dump(summary, open(out / "timing.json", "w"), indent=1)
print(f"{engine}-{voice}-{condition}: first audio median {summary['ttfa_med']*1000:.0f}ms max {summary['ttfa_max']*1000:.0f}ms | RTF median {summary['rtf_med']:.2f}")
