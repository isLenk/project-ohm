# Export and training

Approved conversations become JSONL examples. JSONL is used to train a LoRA adapter on the base model, the adapter is converted to GGUF, and it shows up in the model dropdown.

## 1. Building examples

Examples use **rotation**, from Smarth's `training/discord_dataset.py` (PR #3). Every approved, non-archived conversation is replayed once per person in it. In each replay:

- **That person's messages** become `assistant` turns. The model learns to answer as Ohm in that person's voice.
- **Everyone else's messages** become `user` turns written as `Name: text`. Back-to-back runs from one person are joined, and so are back-to-back user turns.
- **The replayed person's name becomes `Ohm`** anywhere in the text (whole word, case-insensitive, `@` kept). So `@KaptainDoosh are we on?` reads `@Ohm are we on?` in KaptainDoosh's replay, the way people will actually talk to the bot.

Which messages go where:

- **Excluded messages** are left out entirely.
- **Only answers are targets.** A reply counts only if its arrow points at something said since that person's last turn (or at a message carried over, see below). That message, with the rest of its block, is moved to the end of the `user` turn, so every Ohm turn answers the last line before it. At runtime the bot answers the message that pinged it, which is always the last line it sees. A topic start (root arrow) is dropped as `starts a topic`. A reply to something older, or to the person's own earlier line, is dropped as `answers something older`, and so are later lines of a turn that answer something else. Without this, about half the replies in a busy chat answered something a few lines up or nothing at all, and the adapter learned to say whatever fit the chat instead of answering.
- **Bots** are context only, never replayed.
- **Unattached messages** (their unit has no link) still show as context when someone else is replayed, because the bot sees everything said in the channel. They are never trained as replies.
- **Short replies** (`lol`, `ok`, `same`, … under 2 characters after trimming) are mostly dropped as targets. A fixed 10% stays, picked by message id, so the model still says "lol" sometimes.
- **Replies over 600 characters** are dropped as targets.
- **Replies over 4 lines** (`MAX_REPLY_LINES`) are dropped as targets. Long bursts teach turns that never hand back.
- **Links, gifs and attachments** are dropped from targets, message by message. The model can't see or send real ones, so it would make them up. They stay as context in other people's turns.
- **A leading @mention of the person being answered** is taken off a target line (`@Lenk yo` becomes `yo`). The person is whoever the reply arrow points to, else whoever spoke last. The bot answers with a Discord reply, which pings them already. Other mentions stay, and the bot turns them into real pings.
- **Replies with a line that starts like a speaker label** (`Lenk:`, `Ohm:`) are dropped as targets. That's the shape of a transcript, which the bot must never write.
- **Turn order:** a replay starts on a user turn and ends on an assistant turn. A person's opening message, with nothing before it, has nothing to answer, so it is not a target. The bot only ever answers.

**Chunking:** conversations longer than `max_len` are split at turn pairs, repeating the last 2 pairs at the start of the next chunk. The budget is `max_len × 3` characters minus the system prompt. The worker still drops anything that tokenizes over `max_len`.

**Silences:** a conversation is cut into segments wherever nobody spoke for longer than the project's `split_gap_ms` (30 minutes by default), and each segment is replayed on its own (`segment` in the record). The bot only sees the last 30 minutes too.

Apart from moving the answered message to the end, a replay is linear by time, like the bot's own channel history. So the arrows decide what gets trained: arrows the model suggested and nobody checked are trusted as they are.

One exception: a reply whose parent is **before its segment** (in an earlier conversation, or before a silence in this one) gets the parent's block, up to and including the parent message, as a `user` turn right before it. Without it, a reply after a long gap would have nothing before it and never be trained. The carried block is context only: it is never a target, and it is left out of its own author's replay.

### Export record (`/export/jsonl`)

One record per line. `messages` is exactly what gets trained:

```json
{"id": "conv:12:0:1234:0", "conversation_id": 12, "segment": 0, "speaker": "KaptainDoosh", "speaker_id": "1234",
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
system:    the project character's personality from the NAS, like the bot (config/constants.py when the NAS is down)
user:      unanticipatedinflation: we running barotrauma this night? @Ohm
           Zhingstin Ji: Baro could hit
assistant: Mb I was out all day and just got home
user:      ...
assistant: ...
```

At runtime the bot sends the last 30 minutes of channel history the same way: `Name: text` user turns, its own replies as assistant turns, and same-role turns merged (`_merge_same_role`). The only difference is what it appends to the system prompt: the "Things you remember" notes, and a "People who came up" block for anyone the message names (by display name, Discord handle, or a name added on the Memories page). If the personality changes (Characters page, or `config/constants.py` for the offline copy), retrain, because the system prompt is part of every example. Keep its examples as "if someone says X, you'd say Y", not `name: line` pairs: a model that drifts copies a `lenk:`/`Ohm:` block word for word as a transcript.

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
  "target_modules": "all-linear", "lr": 2e-4, "epochs": 1,
  "max_len": 2048, "batch_size": "auto", "grad_accum": "auto",
  "load_in_4bit": true, "seed": 1337, "speakers": null }
```

Use QLoRA (4-bit base) by default so E4B fits on a consumer GPU. `batch_size: auto` means: try the largest batch that fits, and fall back on CUDA out-of-memory. The effective batch is 16 either way.

**Schedule:** warmup is 10% of the steps, then a cosine decay. (A fixed 5 warmup steps on a 26-example run meant 6 steps in all, with the rate still climbing on the last one.) Transformers 5 has no `warmup_ratio`; a float under 1 in `warmup_steps` is the ratio.

**Held-out conversations:** with 20 or more conversations, 10% of them (at least 3) are kept out of training, whole, since each is replayed once per person. The loss on them is measured 8 times per run (`eval_loss`, `best_eval_loss` in progress and done events), and the adapter saved is the checkpoint with the lowest held-out loss, not the last step. Training loss on chat stays high (around 3), so judge a run by the held-out loss and by sample replies.

**Size:** aim for 1,500–3,000 assistant turns. A few hundred only nudges the model, which can weaken its turn-taking without teaching the voice.

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

Only one run at a time. The GPU is also used by llama-server, TTS and STT, so with the bot live a 12–16 GB card won't fit a run. Before starting, the Train modal reads `nvidia-smi`. If free VRAM is under what the run needs (`CURATOR_TRAIN_VRAM_GB`, or when that's unset, 6 GB over the base model's GGUF size: ~11 GB for E4B, ~14 GB for Gemma 4 12B), it lists the GPU processes and you tick which to pause.

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

Adapters left out of a per-request `lora` list count as scale 0 (checked on b12144), so a request names the full set.

**The bot doesn't trust the global scales.** Every reply request names the in-play character's adapter (`configs.ui.model.adapter_id` on the NAS, resolved to a file through the registry) at 1.0 and every other loaded adapter at 0. Its utility calls (intent, web search, memory notes) and all of the Curator's own calls (`llm.chat`) send every adapter at 0, so they run on the base model. The global scales once ended up with two adapters both at 1.0, which stacked them. A character whose adapter isn't loaded yet runs without one, and the engine says to restart llama-server.

So `PUT /models/active` works like this:

1. If the chosen adapter file is already loaded, POST the new scales to `/lora-adapters`. No restart is needed.
2. If it is a newly trained file, restart llama-server once with every registered adapter for that base on `--lora`, plus `--lora-init-without-apply`. Then do step 1.

Update `executables/_run_llama.cmd`, or better, have the controller build the command from the registry.

**Training off the PC** goes through `export.for_cloud(rows)`, never the raw `dataset.jsonl`. Each row keeps only `messages` and a renumbered `conversation_id` (no speaker, speaker id, message ids or row id). In the text, every known person's names and contact details are masked as in `apps/brain/brain/privacy.py`, with stand-ins drawn fresh for each conversation: the same person is one name within a conversation and a different one in the next, so nothing links conversations, and the adapter learns to use whatever names the chat holds rather than particular ones. Ohm keeps his name. With no name list loaded it raises instead of sending. Checked on 2026-11-08 against the 600 rows of run `20261107-183109-ff3b45`, masked with 22 project authors and 9 NAS users: no known name and no Discord id left; 339 links and 5 emails became placeholders.

**Switching base models** (E4B and Gemma 4 12B, say) restarts llama-server on the other model with that model's own vision projector and MTP draft head (`gpu.companions`). A file goes with a model if its name contains the model's name without the quant (`mmproj-Gemma-4-E4B-…-f16.gguf`), or if the model has a folder to itself (`models/gemma-4-12b/` holding `mmproj-F16.gguf` and `mtp-gemma-4-12b-it.gguf`). With an MTP head, `--spec-type draft-mtp -md <mtp> -ngld 99` is added: on a 3090 it took the 12B from ~40 to 66–110 tokens/s. Each base has its own adapters, so a new base runs without one until it's trained. Training uses the selected base's Hugging Face weights (`TRAINABLE_BASES` in `registry.py`: `unsloth/gemma-4-E4B-it`, `unsloth/gemma-4-12b-it`, and for QAT builds such as `Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced`, which ships GGUF only, `google/gemma-4-12B-it-qat-q4_0-unquantized`, the weights it was made from).

## 6. Scoring an adapter

The held-out loss says how well an adapter predicts the group's wording. It can't tell whether Ohm answers what was said: the first adapter had a fine loss and still replied "so they are gonna have a blue one now" to "Can you actually hear me?". So a finished adapter can be scored by a bigger model acting as the judge (`train/evaluate.py`). Open it with **Score it** at the end of a run, or **Score this LoRA…** in the model menu.

**The judge** goes through the brain package (`apps/brain`), role `judge`: Codex first, then the local Qwen model (`BRAIN_ROLE_JUDGE=codex,qwen`, set in Settings › Integrations). The chats go 30 to a call, three calls at once, and anything the judge skipped is asked again in smaller packs (`brain.run_packed`). Codex spends about 4 s and 10k tokens a call however small it is, so one call per chat would be 20 times the cost. The results record what judged (`eval.json` `judge`: `codex`, or the Qwen model's file name). If the judge falls back to the next backend partway through, that isn't seen there; the Curator's log has a `[brain] judge via ...` line per call.

**The local judge** is only used when it's the first backend that can run (Codex not installed, `BRAIN_LOCAL_ONLY=1`, or the role set to `qwen`): `brain.needs_gpu_swap("judge")`. It's `Qwen3.6-27B-Q4_K_M.gguf` (unsloth/Qwen3.6-27B-GGUF, 15.7 GB) in llama.cpp's `models` folder. `CURATOR_JUDGE_MODEL` points elsewhere, `CURATOR_JUDGE_PORT` (5055) moves it. It's left out of the model dropdown: it's never Ohm. On the 3090 it uses about 17 GB. It serves 4 requests at once (`-np 4`, 4096 tokens of context each), about 2 to 3 seconds per verdict each. Starting a score with neither checks out is a 409 `no_judge`: "sign in to Codex in Settings › Integrations or put the Qwen judge model at ...".

**What gets scored:** the last Ohm turn of every held-out example of the run (up to 60), so only adapters trained here can be scored, and only runs with 20+ conversations hold any out. Plus 8 probes shaped like the live messages that went wrong ("Can you actually hear me?", "Tell me something interesting."), asked by whoever talks most in the held-out chats.

**Two passes:**

1. llama-server on the adapter's base answers each context three ways with per-request `lora` scales: no adapter, the adapter at 1.0, and at 0.6. Temperature 1.0 like the bot, a fixed seed per item. This always needs Ohm's llama-server.
2. The judge. For each context it gets the candidates plus the person's real reply, shuffled and labeled A, B, C…, and says which answer the last message (`"answers": [true, false, ...]` in label order; a list, since Codex's structured output wants every key of an object spelled out and chats have 3 or 4 candidates). Then, for the voice, it gets each of Ohm's replies paired with the real one, in random order, and is told one was written by the person and one by an AI imitating them: which did the person write? Temperature 0. With Codex, Ohm's llama-server stays up. With the local Qwen judge, Ohm's model and the judge don't fit in 24 GB together, so Ohm's llama-server stops and the judge starts on its own port, with reasoning off: on the calibration below, thinking took 60 s a verdict and changed nothing.

The voice test was a ranking at first: order all the candidates by how much they sound like someone in the group. The judge put the base model's polished jokes ("flamingos are basically nature's pink traffic cones") above the group's own replies, so the ranking measured wit, not likeness. A blind pair against the real reply can't reward that.

Afterwards Ohm's llama-server is put back the way it was: started again if it was stopped (with the active adapter on), restarted on its old command line if the reply pass had moved it to the adapter's base model, and stopped if it wasn't running before. The result is `eval.json` in the run folder: per variant, the share that answer the last message and, for held-out chats, the share the judge took for the real reply (**passes as real**: 50% means it can't tell them apart, 0% means it always spots the bot), for held-out chats and probes separately, plus every reply with its verdicts.

**Calibration (2026-11-07, Qwen judge):** on 20 held-out chats the judge counted 18 of the real replies as answering, and 5 of 20 real replies swapped in from other chats. Four of those five did fit ("play?" answered by "im joinng"), so it's about 1 in 20 wrong. The real reply's row is the ceiling to compare against: the group doesn't always answer either. Codex hasn't been calibrated the same way yet, so compare scores judged by the same backend.

## 7. Checking replies with the judge

Laying each reply out after the message it answers (section 1) isn't enough on its own. The judge counted only about two thirds of the group's real replies as answering the line before them: the arrow was a guess, or the person was carrying on their own thread. The first adapter trained on that data answered 58% of held-out chats, against 83% for the base model, so it had learned to ignore what was just said.

So before training, the judge can read every reply the export would train on (`train/checks.py`, **Check them** in the Train modal, `POST /train/check-replies`). It sees the reply as it would be trained, after the chat before it, and says whether it answers the last line (`{"answers": true}` per reply). It's the same brain `judge` role as scoring, 30 replies to a call and 90 per round; each round's verdicts are saved as they come, and the job's progress moves per round. Verdicts are stored per reply in the project (`reply_checks`, keyed by the reply's message ids, with `judge` set to what judged), so a reply is checked once, and a later check only reads replies that are new. The export leaves out every reply the judge says doesn't answer, counted as `doesn't answer (judge)`. Unchecked replies are trained as before. An unreadable verdict isn't stored, so the next check tries it again.

With Codex as the judge, Ohm's llama-server isn't touched. Only with the local Qwen judge does it stop for the length of the check and start again afterwards.
