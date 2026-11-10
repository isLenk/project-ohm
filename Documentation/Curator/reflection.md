# Reflection

Every so often Ohm looks back over a stretch of conversation and writes down what he'll want later: things to bring up, inside jokes, his own opinions, questions to ask, how he gets on with each person, and small shifts in his personality. The NAS decides when and keeps the results; the Curator on the PC does the thinking, since it needs the signed-in Codex CLI. The code is `curator_service/app/reflect.py`.

## What the NAS queues

The memory service (`apps/memory`, README "Reflection") checks every 10 minutes whether a character has had at least 40 new messages since the last finished reflection and the newest is over 30 minutes old, so a conversation is reflected on once it's over. It then queues a job covering those message ids. The app's **Reflect now** (`POST /reflections/request` on the NAS) queues whatever is new without waiting. Only one job per character is open at a time. A job left running for an hour goes back to pending, and a failed one is queued again after an hour.

## The worker

When the service starts with `MEMORY_API_URL` set, a thread asks the NAS `GET /reflections/due` every `POLL_MINUTES` (5). With a job there, it claims it (`POST /reflections/{id}/claim`; a 409 means someone else took it), runs it, and calls `POST /reflections/{id}/finish` with the result, or with `error` when anything failed. One reflection runs at a time. `POST /reflect/run-now` polls straight away, in the background, and `GET /reflect/status` says whether it's polling, when it last did, which job is running and how the last one went (api.md, "Reflection").

A job:

1. Reads the messages in the claimed range (`GET /messages`, 2,000 at a time), the people (`GET /users`), how Ohm gets on with them (`GET /relationships`), his traits with 30 days of history (`GET /traits`), and who he is (`GET /characters/{slug}`, the first 600 characters of the personality).
2. Writes the chat out as `Name: text` lines, his own as `Ohm: text`, grouped by channel, with the date and time at the start of each channel and after any pause over 30 minutes. A stretch over 60,000 characters keeps its latest part.
3. Makes one brain call, role `reflect` (Codex, then DeepSeek: `BRAIN_ROLE_REFLECT`), reasoning `high`, up to 2,500 tokens, allowed `BRAIN_TIMEOUT` (15 minutes; a whole evening at high reasoning takes Codex longer than the brain's usual 4), with a JSON schema. The system prompt has who he is, his current traits and the people he knows, and asks for:

   ```json
   {"topics":    [{"text": "ask Kyle how the job interview went", "people": ["Kyle"], "importance": 0.7}],
    "jokes":     [{"text": "'the blue one': Lance picked the wrong door three times", "people": ["Lance"]}],
    "opinions":  [{"text": "Ohm thinks Hollow Knight is overrated: the group argued about it for an hour", "importance": 0.5}],
    "questions": [{"text": "what Kyle does for work", "people": ["Kyle"]}],
    "relationships": [{"person": "Lance", "affinity_delta": 0.1, "notes": "calls him grandpa; the door thing"}],
    "ohm_lines": [{"traits": ["sarcastic", "blunt"], "reception": 0.8, "quote": "..."}],
    "summary": "one or two sentences on what happened"}
   ```

   `ohm_lines` has one entry per line Ohm said: which of his traits it showed, and how the group took it from 0 to 1 (laughing, replying, agreeing or playing along is high; silence, "shut up", being ignored or a change of subject is low). The rules: short and concrete, only things that make sense weeks later to someone who wasn't there, nothing about who owes whom, nothing private like addresses or health unless the person said it as a fact about themself, people by the names the chat uses, and empty lists are fine.

4. Writes it all to the NAS, into tables the engine already reads, so it costs nothing at reply time:

| What | Where | How |
|---|---|---|
| topics, jokes, opinions, questions | `POST /memories`, `kind` `topic` / `joke` / `opinion` / `question` | At most `MAX_PER_KIND` (5) of each per run. `importance` from the answer, else 0.6. `user_ids` from the names: the names in the chat first, then every name in `GET /users` (preferred, display, username, aliases), exact and ignoring case; a name nobody goes by is left out. A note at 0.9 similarity or more to a memory of the same kind (`POST /memories/search`) isn't stored again |
| relationships | `PATCH /relationships/{character}/{user_id}` | `affinity_delta` summed per person and kept within ±`MAX_AFFINITY_STEP` (0.15) per run. The notes are the old ones with the new ones added (`;`-separated, repeats dropped), the oldest dropped past 300 characters. Nothing is sent for a person with no change, or for Ohm himself |
| traits | `PUT /traits/{character}` | See below |
| a note to himself | `POST /memories`, `kind` `self` | See below. It replaces the previous one (`GET /memories?kind=self`, then `DELETE`) |

5. Finishes the job with `{summary, written: {topic, joke, opinion, question, self}, relationships, traits: [{trait, from, to}], backend, ms}`. The NAS logs the summary (`source='reflect'`), where the app shows it. `backend` is the first backend the role could use when it finished; if Codex failed and DeepSeek answered, the `[brain] reflect via ...` log line says so.

If the brain gives no answer, or the NAS fails partway, the job is finished with `error` and the NAS queues it again later. Anything already written stays; the similarity check keeps the second run from writing the same memories twice, but a relationship nudge can land twice.

## The trait math

A trait only moves when at least `MIN_LINES_FOR_TRAIT` (3) of his lines this run showed it. Then, with `r` the mean reception of those lines:

```
value += TRAIT_STEP * (r - 0.5) * 2        TRAIT_STEP = 0.03
```

so a trait whose lines all landed (r = 1) grows by 0.03, one whose lines all fell flat (r = 0) shrinks by 0.03, and a mixed reception barely moves it. The value is kept within the trait's floor and ceiling (the NAS clamps too), and the history row gets a reason like "8 sarcastic lines, reception 0.71". Traits that didn't show, and trait names the model made up, don't move. At 0.03 a run, getting from 0.5 to 0.8 takes ten very good conversations in a row, so his personality drifts over weeks rather than flipping in an evening.

**The note to himself:** after the nudge, each trait's value is compared with its oldest value in the 30-day history (or with its value before this run, for a trait with no history). If any moved more than `SELF_NOTE_MIN_CHANGE` (0.08), one memory of kind `self` says so in his own words: "Lately I've been getting more sarcastic and a bit less wholesome than a month ago." ("a bit" under 0.15). With nothing over the threshold the old note is left alone.

## Knobs

All constants at the top of `reflect.py`:

| Constant | Default | What it does |
|---|---|---|
| `POLL_MINUTES` | 5 | How often the NAS is asked for a job |
| `GAP_MINUTES` | 30 | A pause this long gets a time marker in the transcript |
| `MAX_TRANSCRIPT` | 60000 | Characters of chat sent; the latest part is kept |
| `PERSONALITY_CHARS` | 600 | Of the character's personality, in the prompt |
| `REPLY_TOKENS` | 2500 | `max_tokens` for the call |
| `BRAIN_TIMEOUT` | 900 | Seconds the call may take before the backend gives up and the next is tried |
| `MAX_PER_KIND` | 5 | Memories of one kind per run |
| `DEFAULT_IMPORTANCE` | 0.6 | For a note without one |
| `DUPLICATE` | 0.9 | Similarity at which a note counts as already stored |
| `MAX_AFFINITY_STEP` | 0.15 | Most affinity can move per person per run |
| `NOTES_CHARS` | 300 | Relationship notes are kept to this |
| `TRAIT_STEP` | 0.03 | Most a trait can move per run |
| `MIN_LINES_FOR_TRAIT` | 3 | Lines showing a trait before it moves |
| `SELF_NOTE_MIN_CHANGE` | 0.08 | Drift over the month that's worth a note to himself |

Which model thinks is the brain's `BRAIN_ROLE_REFLECT` (apps/brain/README.md). `BRAIN_LOCAL_ONLY=1` leaves only the local backends, which aren't in the default `reflect` list, so reflections fail until the role is given one.

## Tests

`curator_service/tests/test_reflect.py`: a fake NAS (a dict of method and path to handlers in place of `requests.request`) and a canned brain answer. They cover the transcript and paging, memories per kind with the cap and the duplicate check, names to user ids, the affinity clamp and the notes merge, the trait nudge and its reason, the note to himself only above the threshold, and a failed job when the brain gives nothing.
