# Export and training

Approved conversations become JSONL examples. JSONL is used to train a LoRA adapter on the base model, the adapter is converted to GGUF, and it shows up in the model dropdown.

## 1. Building examples

One example per **unit**, meaning a block or a split line, that meets all of these:

- it is in a conversation that is approved and not archived
- its link is `accepted` or `root`, or `suggested` and accepted on approve
- it has at least one non-excluded message
- its author is not a bot

**Target text:** the unit's non-excluded messages' `content_text`, joined with `\n`. For a block, leave out lines that are split lines, because they are their own units.

**Context:** walk effective parents from the unit up to its root, then reverse so the oldest comes first. Each step contributes the *parent unit*: the block containing the parent message, or the split line itself.

- Skip excluded messages inside a context unit. If a whole unit is excluded, keep walking past it.
- **Approval rule (default):** stop walking at the first unit that is in an unapproved or archived conversation. Context may cross conversation splits, but only into approved ones.
- **Length cap:** if the formatted prompt exceeds `max_len - target_tokens`, drop context from the oldest end, but always keep the root.

A root unit becomes an example with empty context. That teaches the model how this person opens a topic.

### Export record (`/export/jsonl`)

This is the model-agnostic format. Write one record per line.

```json
{"id": "blk:881", "speaker": "KaptainDoosh", "speaker_id": "1234",
 "context": [
   {"speaker": "unanticipatedinflation", "text": "we running barotrauma this night? @KaptainDoosh"},
   {"speaker": "Zhingstin Ji", "text": "Baro could hit"}
 ],
 "target": "I’m down to baro",
 "conversation_id": 12, "source_message_ids": ["...", "..."]}
```

### Balancing and cleanup at export time

Apply these in order, and report each count in `GET /export/preview`:

1. **Duplicates:** if two targets by the same author have cosine ≥ 0.95, keep the one with the longer context.
2. **Author cap:** if an author has more than `author_cap` (default 30%) of the examples, downsample them at random with a fixed seed (the dataset hash) until they are at the cap. The UI's author-skew bar shows the pre-cap shares.
3. **Speaker filter:** optional `speakers: [...]` in the training config limits targets to those people.

`dataset_sha` is the sha256 of the final JSONL. It is stored on the run, so a run can always be traced back to its exact data.

## 2. Prompt formatting

Gemma 4's chat template has a real system turn (`<|turn>system`) and does not force user and model turns to alternate. Earlier Gemma versions lacked both, so ignore older guides on this. Format each record as three turns:

```
system: You are {speaker} in a Discord group chat. Reply as {speaker} would.
user:   unanticipatedinflation: we running barotrauma this night? @KaptainDoosh
        Zhingstin Ji: Baro could hit
model:  I’m down to baro
```

All context goes into one user turn as "Name: text" lines. Keep it that way even though the template would allow several user turns, because the bot's live prompt sends history the same way.

- Render it with the base model's own chat template from the HF tokenizer (`tokenizer.apply_chat_template`). Never hand-write the special tokens.
- Compute loss on the model turn only (assistant-only masking, for example TRL's completion-only collator).
- At inference, the bot's prompt must use the same "You are {speaker}" framing and "Name: text" history lines, or the adapter won't fire the way it was trained. Coordinate with `controller/wrappers/openai.py`, which already merges same-role turns (`_merge_same_role`).

## 3. Training weights (important)

The live model is `Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q5_K_M.gguf`, served by `llama-server` (`executables/_run_llama.cmd`). **A quantized GGUF cannot be trained.** LoRA needs unquantized Hugging Face weights.

Checked on 2026-11-06:

- **The HauhauCS repo ships GGUF only.** [`HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive`](https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive) has GGUF quants and an mmproj file. It has no safetensors. Its model card lists `google/gemma-4-e4b-it` as the base and tags it `abliterated`.
- **So train on the original instruct model.** Train on `google/gemma-4-e4b-it`, which is under the Gemma license and needs an HF token with the license accepted. [`unsloth/gemma-4-E4B-it`](https://huggingface.co/unsloth/gemma-4-E4B-it) is an ungated copy. [`unsloth/gemma-4-E4B-it-unsloth-bnb-4bit`](https://huggingface.co/unsloth/gemma-4-E4B-it-unsloth-bnb-4bit) is a pre-quantized 4-bit copy for QLoRA.
- **Then apply the adapter to the HauhauCS GGUF.** Abliteration edits the weights only slightly and keeps every tensor name and shape, so the adapter will load. It was still trained against slightly different weights. Compare samples from the adapter on the plain Gemma GGUF and on the HauhauCS one before trusting it. Show a "trained on a different base" note in the model dropdown.
- **Library support exists.** Community guides and adapter repos show Gemma 4 E4B LoRA and QLoRA with Unsloth (`FastModel.get_peft_model`) and with PEFT. Use Unsloth to train, but **don't use its `save_pretrained_gguf()`** for Gemma 4: adapter authors report it is broken. Convert with llama.cpp instead (section 5).
- **Text only.** Gemma 4 E4B is multimodal (vision and audio). Put LoRA only on the language-model layers, not the vision or audio towers.

Set `base_models.hf_path` to the HF repo id or local folder used for training. `POST /train` returns `409 base_not_trainable` while it is empty.

## 4. The training worker

`POST /train` writes the JSONL, inserts a `train_runs` row (`queued`), and starts `python -m app.train.worker --run <id>` as a **subprocess**. Never train inside the API process. The API stays responsive and cancel is a kill.

Default config, matching the Train modal:

```json
{ "method": "lora", "r": 16, "alpha": 32, "dropout": 0.05,
  "target_modules": "all-linear", "lr": 2e-4, "epochs": 3,
  "max_len": 1024, "batch_size": "auto", "grad_accum": "auto",
  "load_in_4bit": true, "seed": 1337, "speakers": null }
```

Use QLoRA (4-bit base) by default so E4B fits on a consumer GPU. `batch_size: auto` means: try the largest batch that fits, and fall back on CUDA out-of-memory.

### Stages

The modal lists these in order:

| Stage | Work |
|---|---|
| `export` | Build the JSONL (section 1) |
| `tokenize` | Apply the chat template and tokenize; drop examples longer than `max_len` and count them |
| `train` | The training loop |
| `save` | Save the PEFT adapter, convert it to GGUF, register it |

### Progress events

The worker writes one JSON line per event to the run's `log_path`. `GET /train/runs/{id}/events` tails that file as SSE, starting with the last known state. That is what makes "Run in background" and reopening the modal work: progress lives on disk, not in the HTTP connection.

```
event: progress
data: {"stage":"train","stage_index":2,"stages":4,"epoch":2,"epochs":3,
       "step":412,"total_steps":640,"loss":1.31,"first_loss":2.40,
       "elapsed_s":872,"eta_s":360,"overall":0.71}

event: stage
data: {"stage":"save","elapsed_s":1250}

event: done
data: {"status":"succeeded","adapter_id":"...","final_loss":1.12,"elapsed_s":1312}

event: error
data: {"status":"failed","error":"CUDA out of memory at step 3","log_tail":"..."}
```

- Emit `progress` at most every 2 seconds, and on every logging step.
- `eta_s`: moving average of seconds per step × remaining steps, plus a fixed estimate for `save` (measure it once and store it in registry settings).
- `overall`: weight the stages export 2%, tokenize 3%, train 85%, save 10%.
- Losses for the sparkline: keep every logged `loss` in the run log. The modal fetches `GET /train/runs/{id}` for history, then follows the stream.

### Cancel

`POST /train/runs/{id}/cancel`: send SIGTERM (`CTRL_BREAK_EVENT` on Windows) to the subprocess. Wait 10 s, then kill it. Set `status='cancelled'`. Leave partial checkpoints in the run folder, but do not register an adapter.

Only one run at a time. The GPU is also used by llama-server, TTS and STT, so with the bot live a 12–16 GB card won't fit a run. Before starting, the Train modal reads `nvidia-smi`. If free VRAM is under `CURATOR_TRAIN_VRAM_GB`, it lists the GPU processes and you tick which to pause.

- **llama-server:** its command line is saved, and it restarts after the run with every adapter for that model loaded. The new adapter is switched on through `/lora-adapters`.
- **Other processes (TTS, the bot):** they are stopped and listed. You start them again yourself, because relaunching them headless would hide their consoles.

## 5. After training

1. Save the PEFT adapter to `<app data>/adapters/<run_id>/peft/`.
2. Convert it with llama.cpp. The script only needs the base model's config files, not its weights. Pass `--base <folder>` for a local folder, or `--base-model-id <repo id>` to read the config from the Hub:

   ```
   python llama.cpp/convert_lora_to_gguf.py <peft dir> --base-model-id google/gemma-4-e4b-it --outtype f16 --outfile <app data>/adapters/<run_id>/adapter.gguf
   ```

3. Insert an `adapters` row. It now appears under "LoRA adapters you've trained" in the dropdown.
4. Add `(run_id, dataset_sha)` to the project's `project_runs`.

### Serving the adapter

This was checked against the current llama.cpp server README.

- Adapter files are loaded only at startup: `--lora a.gguf,b.gguf` (comma-separated, or repeat `--lora`).
- Start with `--lora-init-without-apply` so every adapter loads with scale 0, which means no effect until chosen.
- **Pick an adapter without restarting.** Use either:
  - `GET /lora-adapters` lists the loaded adapters with their `id`.
  - `POST /lora-adapters` with `[{"id": 0, "scale": 1.0}]` sets the global scale.
  - Or per request: send `"lora": [{"id": 0, "scale": 1.0}]` in the completion body. Requests with different LoRA settings are not batched together.

So `PUT /models/active` works like this:

1. If the chosen adapter file is already loaded, POST the new scales to `/lora-adapters`. No restart is needed.
2. If it is a newly trained file, restart llama-server once with every registered adapter for that base on `--lora`, plus `--lora-init-without-apply`. Then do step 1.

Update `executables/_run_llama.cmd`, or better, have the controller build the command from the registry.
