"""Scores out/<run>/ folders: word error (Whisper large-v3) and naturalness (UTMOS22, 1-5). bench_tts.py's venv.
    python score.py [run ...]
? UTMOS marks tagged lines ([sigh]) down, so compare engines on plain lines."""
import json, re, sys
from pathlib import Path
import truststore; truststore.inject_into_ssl()  # WARP re-signs TLS
import numpy as np, soundfile as sf, librosa, torch, jiwer
from faster_whisper import WhisperModel
HERE = Path(__file__).parent
NUM = {"2": "two", "1": "one", "20": "twenty", "100": "one hundred", "t1": "t one"}
def norm(s):
    s = s.lower().replace("%", " percent").replace("+", " plus")
    s = re.sub(r"[^\w\s']", " ", s)
    return " ".join(NUM.get(w, w) for w in s.split())
whisper = WhisperModel("large-v3", device="cuda", compute_type="float16")
mos = torch.hub.load("tarepan/SpeechMOS:v1.2.0", "utmos22_strong", trust_repo=True).cuda().eval()
runs = sys.argv[1:] or sorted(p.name for p in (HERE / "out").iterdir() if (p / "timing.json").exists())
for run in runs:
    d = HERE / "out" / run; t = json.load(open(d / "timing.json"))
    wers, moss = [], []
    for r in t["rows"]:
        a, sr = sf.read(d / f"{r['i']:02d}.wav"); a = a.astype(np.float32)
        a16 = librosa.resample(a, orig_sr=sr, target_sr=16000)
        heard = " ".join(s.text for s in whisper.transcribe(a16, language="en", beam_size=5)[0])
        r["heard"] = heard
        r["wer"] = jiwer.wer(norm(r["text"]), norm(heard)) if norm(heard) else 1.0
        with torch.no_grad():
            r["mos"] = float(mos(torch.from_numpy(a16).unsqueeze(0).cuda(), 16000).item())
        wers.append(r["wer"]); moss.append(r["mos"])
    t["wer_mean"], t["mos_mean"] = float(np.mean(wers)), float(np.mean(moss))
    json.dump(t, open(d / "timing.json", "w"), indent=1)
    print(f"{run:<28} words wrong {t['wer_mean']*100:5.1f}%  naturalness {t['mos_mean']:.2f}/5  "
          f"| first audio {t['ttfa_med']*1000:5.0f}ms  RTF {t['rtf_med']:.2f}")
