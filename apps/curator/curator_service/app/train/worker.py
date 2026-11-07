"""Training subprocess: python -m curator_service.app.train.worker --run-dir <dir> [--simulate]

Reads config.json and dataset.jsonl from the run folder, trains a LoRA with Unsloth + TRL,
saves it, converts it to GGUF with llama.cpp, and writes progress to events.jsonl.
Install the training extras first: pip install -r curator_service/requirements-train.txt

Written against Unsloth's Gemma 4 support and llama.cpp's convert_lora_to_gguf.py. It has
not been run on a GPU yet, so expect to adjust it on the first real run.
"""

import argparse
import json
import math
import os
import random
import subprocess
import sys
import time
from pathlib import Path

STAGES = ["export", "tokenize", "train", "save"]
WEIGHTS = {"export": 0.02, "tokenize": 0.03, "train": 0.85, "save": 0.10}
SAVE_ESTIMATE_S = 90
PROGRESS_EVERY_S = 2.0


class Reporter:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.start = time.time()
        self.first_loss = None
        self.step_times: list[float] = []
        self.last_emit = 0.0

    def emit(self, event: str, **data) -> None:
        with open(self.run_dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": event, "t": time.time(), **data}) + "\n")

    def elapsed(self) -> int:
        return int(time.time() - self.start)

    def stage(self, name: str) -> None:
        self.emit("stage", stage=name, stage_index=STAGES.index(name), stages=len(STAGES), elapsed_s=self.elapsed())
        self.progress(name, 0.0, force=True)

    def progress(self, stage: str, fraction: float, force: bool = False, **extra) -> None:
        now = time.time()
        if not force and now - self.last_emit < PROGRESS_EVERY_S:
            return
        self.last_emit = now
        i = STAGES.index(stage)
        done = sum(WEIGHTS[s] for s in STAGES[:i])
        overall = min(1.0, done + WEIGHTS[stage] * max(0.0, min(1.0, fraction)))
        loss = extra.get("loss")
        if loss is not None and self.first_loss is None:
            self.first_loss = loss
        self.emit("progress", stage=stage, stage_index=i, stages=len(STAGES), overall=round(overall, 4),
                  elapsed_s=self.elapsed(), first_loss=self.first_loss, **extra)

    def eta(self, step: int, total: int) -> int:
        self.step_times.append(time.time())
        recent = self.step_times[-30:]
        if len(recent) < 2:
            return 0
        per_step = (recent[-1] - recent[0]) / (len(recent) - 1)
        return int(per_step * max(0, total - step) + SAVE_ESTIMATE_S)


def simulate(run_dir: Path, rep: Reporter, cfg: dict) -> None:
    rep.stage("tokenize")
    time.sleep(1)
    rep.progress("tokenize", 1.0, force=True, dropped_too_long=0)
    rep.stage("train")
    total = max(20, cfg["examples"] * cfg["epochs"] // 2)
    loss = 2.4
    for step in range(1, total + 1):
        time.sleep(0.25)
        loss = max(0.9, loss * 0.985 + random.uniform(-0.02, 0.02))
        epoch = math.ceil(step / (total / cfg["epochs"]))
        rep.progress("train", step / total, force=step == total, step=step, total_steps=total, epoch=epoch,
                     epochs=cfg["epochs"], loss=round(loss, 4), eta_s=rep.eta(step, total))
    rep.stage("save")
    out = run_dir / "adapter.gguf"
    out.write_bytes(b"GGUF-simulated")
    time.sleep(1)
    rep.emit("done", status="succeeded", adapter_gguf=str(out), peft_dir=None, final_loss=round(loss, 4),
             elapsed_s=rep.elapsed())


def train(run_dir: Path, rep: Reporter, cfg: dict) -> None:
    from unsloth import FastModel  # import first: Unsloth patches transformers on import
    from datasets import Dataset
    from transformers import TrainerCallback
    from trl import SFTConfig, SFTTrainer

    try:
        from unsloth.chat_templates import train_on_responses_only
    except ImportError:
        train_on_responses_only = None

    rep.stage("tokenize")
    model, tokenizer = FastModel.from_pretrained(
        model_name=cfg["hf_path"], max_seq_length=cfg["max_len"], load_in_4bit=cfg["load_in_4bit"],
        full_finetuning=False,
    )
    text_tok = getattr(tokenizer, "tokenizer", tokenizer)
    rows = [json.loads(line) for line in (run_dir / "dataset.jsonl").read_text(encoding="utf-8").splitlines() if line]
    texts, dropped = [], 0
    for i, r in enumerate(rows):
        text = tokenizer.apply_chat_template(r["messages"], tokenize=False, add_generation_prompt=False)
        if len(text_tok(text, add_special_tokens=False)["input_ids"]) > cfg["max_len"]:
            dropped += 1
            continue
        texts.append(text)
        rep.progress("tokenize", (i + 1) / len(rows), dropped_too_long=dropped)
    rep.progress("tokenize", 1.0, force=True, dropped_too_long=dropped)
    if not texts:
        raise RuntimeError(f"Every example is longer than max_len ({cfg['max_len']} tokens).")
    dataset = Dataset.from_dict({"text": texts})

    model = FastModel.get_peft_model(
        model, finetune_vision_layers=False, finetune_language_layers=True, finetune_attention_modules=True,
        finetune_mlp_modules=True, r=cfg["r"], lora_alpha=cfg["alpha"], lora_dropout=cfg["dropout"], bias="none",
        random_state=cfg["seed"],
    )

    class Progress(TrainerCallback):
        def __init__(self):
            self.loss = None

        def on_log(self, args, state, control, logs=None, **kw):
            if logs and "loss" in logs:
                self.loss = round(float(logs["loss"]), 4)
            self._send(state, force=True)

        def on_step_end(self, args, state, control, **kw):
            self._send(state)
            if (run_dir / "events.jsonl").exists() and _cancelled(run_dir):
                control.should_training_stop = True

        def _send(self, state, force=False):
            total = state.max_steps or 1
            rep.progress("train", state.global_step / total, force=force, step=state.global_step, total_steps=total,
                         epoch=math.ceil(state.epoch or 0) or 1, epochs=cfg["epochs"], loss=self.loss,
                         eta_s=rep.eta(state.global_step, total))

    attempts = [(4, 4), (2, 8), (1, 16)]
    if cfg["batch_size"] != "auto":
        attempts = [(int(cfg["batch_size"]), int(cfg["grad_accum"]) if cfg["grad_accum"] != "auto" else 1)]
    rep.stage("train")
    callback = Progress()
    for bs, ga in attempts:
        args = dict(
            dataset_text_field="text", per_device_train_batch_size=bs, gradient_accumulation_steps=ga,
            num_train_epochs=cfg["epochs"], learning_rate=cfg["lr"], logging_steps=1, warmup_steps=5,
            optim="adamw_8bit", lr_scheduler_type="linear", seed=cfg["seed"], report_to="none",
            output_dir=str(run_dir / "checkpoints"), save_strategy="no",
        )
        try:
            sft_args = SFTConfig(max_length=cfg["max_len"], **args)
        except TypeError:  # older TRL
            sft_args = SFTConfig(max_seq_length=cfg["max_len"], **args)
        trainer = SFTTrainer(model=model, tokenizer=tokenizer, train_dataset=dataset, args=sft_args, callbacks=[callback])
        if train_on_responses_only:
            trainer = train_on_responses_only(trainer, instruction_part="<|turn>user\n", response_part="<|turn>model\n")
        try:
            trainer.train()
            break
        except RuntimeError as e:
            if "out of memory" not in str(e).lower() or (bs, ga) == attempts[-1]:
                raise
            import torch

            torch.cuda.empty_cache()
            rep.emit("note", message=f"Out of memory at batch size {bs}; retrying smaller.")
    if _cancelled(run_dir):
        return

    rep.stage("save")
    peft_dir = run_dir / "peft"
    model.save_pretrained(str(peft_dir))
    tokenizer.save_pretrained(str(peft_dir))
    rep.progress("save", 0.4, force=True)
    gguf = convert(peft_dir, cfg, run_dir)
    rep.progress("save", 1.0, force=True)
    rep.emit("done", status="succeeded", adapter_gguf=str(gguf), peft_dir=str(peft_dir),
             final_loss=callback.loss, elapsed_s=rep.elapsed())


def convert(peft_dir: Path, cfg: dict, run_dir: Path) -> Path:
    llama_dir = Path(os.getenv("LLAMA_CPP_DIR") or Path(__file__).resolve().parents[6] / "llama.cpp")
    script = llama_dir / "convert_lora_to_gguf.py"
    if not script.exists():
        raise RuntimeError(f"Can't find {script}. Set LLAMA_CPP_DIR to your llama.cpp checkout.")
    out = run_dir / "adapter.gguf"
    base = cfg["hf_path"]
    base_args = ["--base", base] if Path(base).is_dir() else ["--base-model-id", base]
    cmd = [sys.executable, str(script), str(peft_dir), *base_args, "--outtype", "f16", "--outfile", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out.exists():
        raise RuntimeError("Converting the adapter to GGUF failed:\n" + (result.stderr or result.stdout)[-2000:])
    return out


def _cancelled(run_dir: Path) -> bool:
    try:
        tail = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()[-5:]
    except OSError:
        return False
    return any('"event": "cancelled"' in line for line in tail)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--simulate", action="store_true")
    a = ap.parse_args()
    cfg = json.loads((a.run_dir / "config.json").read_text())
    rep = Reporter(a.run_dir)
    try:
        (simulate if a.simulate else train)(a.run_dir, rep, cfg)
    except Exception as e:  # anything: report it, the manager marks the run failed
        import traceback

        traceback.print_exc()
        rep.emit("error", status="failed", error=f"{type(e).__name__}: {e}"[:2000])
        sys.exit(1)


if __name__ == "__main__":
    main()
