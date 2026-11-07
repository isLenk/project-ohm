# Export and training

Approved conversations become JSONL examples. JSONL is used to train a LoRA adapter on the base model, the adapter is converted to GGUF, and it shows up in the model dropdown.

## 1. Building examples

Examples use **rotation**, from Smarth's `training/discord_dataset.py` (PR #3). Every approved, non-archived conversation is replayed once per person in it. In each replay:

- **That person's messages** become `assistant` turns. The model learns to answer as Ohm in that person's voice.
- **Everyone else's messages** become `user` turns written as `Name: text`. Back-to-back runs from one person are joined, and so are back-to-back user turns.
- **The replayed person's name becomes `Ohm`** anywhere in the text (whole word, case-insensitive, `@` kept). So `@KaptainDoosh are we on?` reads `@Ohm are we on?` in KaptainDoosh's replay, the way people will actually talk to the bot.

Which messages go where:

- **Excluded messages** are left out entirely.
- **Bots** are context only, never replayed.
- **Unattached messages** (their unit has no link) still show as context when someone else is replayed, because the bot sees everything said in the channel. They are never trained as replies.
- **Short replies** (`lol`, `ok`, `same`, … under 2 characters after trimming) are mostly dropped as targets. A fixed 10% stays, picked by message id, so the model still says "lol" sometimes.
- **Replies over 600 characters** are dropped as targets.
- **Turn order:** a replay starts on a user turn and ends on an assistant turn. A person's opening message, with nothing before it, is not a target. The bot only ever answers.

**Chunking:** conversations longer than `max_len` are split at turn pairs, repeating the last 2 pairs at the start of the next chunk. The budget is `max_len × 3` characters minus the system prompt. The worker still drops anything that tokenizes over `max_len`.

The thread links don't shape the context. A replay is linear by time, like the bot's own channel history.

### Export record (`/export/jsonl`)

One record per line. `messages` is exactly what gets trained:

```json
{"id": "conv:12:1234:0", "conversation_id": 12, "speaker": "KaptainDoosh", "speaker_id": "1234",
 "chunk": 0, "chunks": 1, "assistant_turns": 1, "source_message_ids": ["...", "..."],
 "messages": [
   {"role": "system", "content": "<Ohm's personality>"},
   {"role": "user", "content": "unanticipatedinflation: we running barotrauma this night? @Ohm\nZhingstin Ji: Baro could hit"},
   {"role": "assistant", "content": "Mb I was out all day and just got home\nI’m down to baro"}
 ]}
```

### Balancing at export time

Apply these, and report each count in `GET /export/preview` (`dropped`):

1. **Speaker filter:** optional `speakers: [...]` in the training config limits which people are replayed. Everyone else is still context.
2. **Author cap:** nobody gets much more than `author_cap` (default 30%) of all assistant turns. Whole examples from over-cap people are dropped at random with a fixed seed (from the example ids). With n people the cap is at least 1/n, and everyone keeps at least one example. The UI's author-skew bar shows assistant-turn shares before and after the cap.

`dataset_sha` is the sha256 of the final JSONL. It is stored on the run, so a run can always be traced back to its exact data.

## 2. Prompt formatting

The format is the one the bot already sends (`controller/wrappers/openai.py`, `modules/discord/DiscordClient.py`):

```
system:    Ohm's personality (config/constants.py, models["ohm"]["personality"].strip())
user:      unanticipatedinflation: we running barotrauma this night? @Ohm
           Zhingstin Ji: Baro could hit
assistant: Mb I was out all day and just got home
user:      ...
assistant: ...
```

At runtime the bot sends the last 30 minutes of channel history the same way: `Name: text` user turns, its own replies as assistant turns, and same-role turns merged (`_merge_same_role`). The only difference is the "Things you remember" notes it appends to the system prompt. If the personality text in `config/constants.py` changes, retrain, because the system prompt is part of every example.

- Render it with the base model's own chat template from the HF tokenizer (`tokenizer.apply_chat_template`). Gemma 4 has a real system turn, so ignore older Gemma guides on this. Never hand-write the special tokens.
- Compute loss on the assistant turns only (`train_on_responses_only`), so every Ohm turn in a multi-turn example is trained and no user turn is.

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
  "max_len": 2048, "batch_size": "auto", "grad_accum": "auto",
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
