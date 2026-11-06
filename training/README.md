# Training data: Discord group chat → Ohm

`discord_dataset.py` turns DiscordChatExporter JSON into fine-tuning data. Every
conversation goes through manual review before it's trained on. Standard library only.

```
DiscordChatExporter JSON
   │  import
   ▼
data/messages.jsonl      read-only: one cleaned message per line
data/speakers.json       editable: aliases for each person
   │  segment            (auto split; never touches reviewed work)
   ▼
data/conversations.json  editable: what the review UI changes
   │  validate           (run after every UI save)
   │  build              (only status = "reviewed")
   ▼
out/train.jsonl  out/val.jsonl  out/build_report.json
```

`data/` and `out/` are gitignored. They contain your friends' messages, so don't commit them.

## Usage

```bash
# 1. Export (JSON format). Get consent from the group first.
DiscordChatExporter.Cli export -t <TOKEN> -c <CHANNEL_ID> -f Json -o raw/

# 2. Import. Safe to rerun with newer exports: new messages are added, existing ones refreshed.
python training/discord_dataset.py import raw/

# 3. Auto-split into conversations
python training/discord_dataset.py segment                 # assigns only messages not yet in a conversation
python training/discord_dataset.py segment --reset-auto    # also re-split conversations still marked "auto"

# 4. Review in the UI, then check the file
python training/discord_dataset.py validate        # exit code 1 on errors
python training/discord_dataset.py validate --fix  # sort messages by time, drop empty conversations

# 5. Build the training set (whole-group rotation)
python training/discord_dataset.py build
python training/discord_dataset.py build --status auto,reviewed   # quick test before review is done
python training/discord_dataset.py build --voice person:lance     # one person's voice instead
```

Useful `build` flags: `--max-speaker-share 0.25`, `--min-target-turns 2`, `--keep-short-frac 0.1`,
`--max-chars 6000` (≈1800 tokens per example), `--overlap-pairs 2`, `--val 0.08`,
`--rename-target-to Ohm`, `--require-aliases`, `--no-meta`, `--system-file`.
Run `build -h` for all of them.

## File formats

All IDs are Discord snowflakes **stored as strings**. They're larger than JavaScript's safe
integer range, so the UI must never parse them as numbers.

### `data/messages.jsonl` (read-only)

```json
{"id":"1100000000000000001","channel_id":"…","channel_name":"general","guild_id":"…",
 "ts":"2026-10-12T21:00:00.000Z","author_id":"…","author":"lance_xd","author_display":"Lance",
 "text":"only if @lance_xd doesn't throw again","raw_content":"only if <@…> doesn't throw again",
 "reply_to":"110…" | null,"mentions":["…"],"reactions":{"💀":3},"attachments":["death.png"],
 "edited":false,"flags":[]}
```

- **`text`**: cleaned content. Mentions become `@username`, URLs become `[link]`, custom emoji become `:name:`. An attachment with no text becomes `[image]` or `[file]`.
- **`flags`**: `bot`, `system`, `command`, `link_only`, `empty`, `attachment_only`.
  - Flagged messages, except `attachment_only`, are left out of auto segmentation. A reviewer can still drag them into a conversation.
  - Flagged messages, including `attachment_only`, are never trained on as replies. They only appear as context.
- Rows aren't merged or deleted. Back-to-back messages from the same person stay separate, so the UI can move each one on its own. Merging happens at build time.

### `data/conversations.json` (the UI edits this)

```json
{
  "schema_version": 1,
  "updated_at": "2026-11-06T20:00:00Z",
  "conversations": [
    {
      "id": "<channel_id>-<first_message_id>",
      "channel_id": "…",
      "status": "auto",
      "message_ids": ["…", "…"],
      "tags": ["banter", "gaming"],
      "notes": "",
      "skip_target": [],
      "edits": {"<message_id>": "rewritten text"},
      "auto": {"start_reason": "gap", "gap_before_min": 42.0, "reply_bridges": 1, "created_at": "…"},
      "suggested_links": [{"message_id": "…", "reply_to": "…", "conversation_id": "…" | null}]
    }
  ],
  "excluded": ["<message_id>"]
}
```

| Field | Who writes it | Meaning |
|---|---|---|
| `id` | script / UI | Any unique string. Keep it stable once reviewed. For new conversations, `<channel_id>-<first_message_id>` is a good choice |
| `status` | UI | `auto` (not reviewed), `reviewed` (goes into training), `rejected` (never used, and never re-split) |
| `message_ids` | UI | Messages in this conversation. Order doesn't matter: build sorts by time, and `validate --fix` re-sorts |
| `tags`, `notes` | UI | Free-form. Tags are copied into each example's `meta` |
| `skip_target` | UI | Messages that stay as context but are never trained on as a reply (e.g. a bad joke) |
| `edits` | UI | Replacement text for a message, used in training instead of `text`. `""` removes the message from training |
| `excluded` | UI | Messages removed from every conversation, which `segment` will never put back |
| `auto` | script | Why the auto split started here. Display only |
| `suggested_links` | script | Replies that point into another conversation (or into none). These are hints for connecting conversations |

**Rules the UI must keep** (`validate` checks them, and `build` refuses to run until they hold):
1. A message is in at most one conversation.
2. A message is not in both a conversation and `excluded`.
3. Every `message_id` exists in `messages.jsonl`.
4. `skip_target` and `edits` only reference messages in that conversation.
5. `status` is `auto`, `reviewed` or `rejected`.

**How UI actions map to the file:**
- **Drag a message out:** remove it from `message_ids`. If it should never come back, add it to `excluded`. Otherwise leave it unassigned and the next `segment` may place it.
- **Drag a message in:** add its ID to `message_ids`, removing it from wherever it was before, including `excluded`.
- **Connect two conversations:** append B's `message_ids` (plus its `skip_target` and `edits`) to A, then delete B.
- **Split a conversation:** move the messages into a new conversation with a new unique `id`.
- **Unassigned messages** are any message IDs that are neither in a conversation nor in `excluded`. Show them as a pool.

Reviewed and rejected conversations are never changed by `segment`, even with `--reset-auto`.
The file has no locking, so if two people review at the same time, compare `updated_at`
before saving, or give each reviewer different channels.

### `data/speakers.json` (editable)

```json
{"schema_version": 1, "speakers": {
  "<author_id>": {"name": "lance_xd", "alias": "Lance", "variants": ["lance_xd", "Lance", "lanceyy"],
                  "is_bot": false, "message_count": 812}}}
```

- **`alias`**: the name used in training, both as the speaker label and in place of any of their `variants` in message text. `null` keeps the username. Set fake names here to pseudonymize; `--require-aliases` enforces it.
- **`variants`**: every name people call this person. Add nicknames by hand. Variants shorter than 3 characters are ignored, so short names don't replace common words.

## What `build` produces

Every reviewed conversation is replayed once per participant (**rotation**):
- **That person's messages** become `assistant` turns.
- **Everyone else's messages** become `user` turns labeled `name: text`. Back-to-back messages are merged, and their name in other people's messages becomes `Ohm`.
- **Turn order:** examples start on a user turn and end on an assistant turn, as Gemma-style templates require.
- **Filtering:** rotations with fewer than `--min-target-turns` replies are skipped. Short replies (`lol`, `ok`, …) are mostly dropped, with 10% kept. Replies over `--max-reply-chars` are dropped.
- **Chunking:** long conversations are split at turn boundaries into chunks of about `--max-chars`, repeating `--overlap-pairs` turns between chunks.
- **Speaker cap:** no speaker gets much more than `--max-speaker-share` of all assistant turns. The cap is approximate on small data.
- **Validation split:** train/val is split **by conversation**, so rotations of one conversation never end up on both sides.
- **System prompt:** taken from `config/constants.py` (`models["ohm"]["personality"]`), exactly as the bot sends it.

```json
{"messages":[{"role":"system","content":"<ohm personality>"},
  {"role":"user","content":"mia: only if @Ohm doesn't throw again"},
  {"role":"assistant","content":"that was ONE time\nliterally once"}],
 "meta":{"conv_id":"…","target":"<author_id>","target_label":"lance_xd","chunk":0,"n_chunks":1,
         "assistant_turns":1,"assistant_message_ids":["…"],"score":3,"tags":[]}}
```

`meta.score` is the total reactions on the trained replies. Use it to filter or weight examples, or to pick DPO pairs later.
`build_report.json` records the flags used, why things were dropped, and assistant turns per speaker before and after the cap.

## Match the runtime format

Training only helps if the model sees the same shape at runtime. Right now
`controller/wrappers/openai.py` sends the raw message with **no speaker name**, plus a fixed
`assistant: "aight lol"` turn and a `"Relevant information: …"` memory turn. Before training,
either change the bot to send `name: text` user turns with recent channel history, or change
`--label` / the build to match what the bot actually sends.
