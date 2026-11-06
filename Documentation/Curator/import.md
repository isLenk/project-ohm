# Import

"Import file" takes a Discord chat export and turns it into messages, blocks, conversations and first-pass links.

## Supported input

Version 1 reads **DiscordChatExporter JSON** (`--format Json`), one channel per file. This is an assumption. Confirm with Lance which exporter he uses before writing the parser. Keep the parser behind an interface so a second format (for example, a backfill from our own bot with `discord.py`'s `channel.history()`) can be added later.

Fields used from each message:

| Export field | Column |
|---|---|
| `id` | `messages.id` |
| `type` (`Default`, `Reply`, others) | `messages.type` |
| `timestamp`, `timestampEdited` | `ts`, `edited_ts` (parse ISO 8601 with offset, store UTC ms) |
| `content` | `content` |
| `author.id`, `.name`, `.nickname`, `.isBot`, `.avatarUrl` | `authors` row |
| `mentions[].id` | `mentions` |
| `reference.messageId` | `reply_to_id` |
| `attachments`, `embeds` | `attachments` (JSON, kept for display only) |

Top-level `guild` and `channel` objects fill `sources`.

## Pipeline

Run the whole import inside one transaction. If any step fails, nothing is written.

1. **Hash the file.** If `sources.sha256` already has it, return `409 already_imported`.
2. **Upsert authors.** New authors get the next free `color_index`. Existing authors keep theirs, so colors and lanes never shuffle between imports.
3. **Insert messages.** Skip ids that already exist; overlapping exports are fine. Build `content_text` by replacing `<@id>`, `<@!id>`, `<#id>`, `<@&id>` with readable names.
4. **Auto-exclude.** Set `excluded = 1` with a reason for:
   - bot authors → `bot`
   - system message types (joins, pins, boosts) → `system`
   - empty content with no attachment → `empty`
   - any enabled `filter_rules` match → `filter:<id>`

   Excluded messages stay in the feed, dimmed. The user can un-exclude.
5. **Build blocks** (below).
6. **Build conversations** (below).
7. **Native links.** For every message with `reply_to_id` that exists in the project, write a link with status `accepted`, origin `discord`. Put it on the **block** if the reply is the block's first message and no other message in the block has a different native reply. Otherwise put it on the message, which makes it a split line.
8. **Suggestions and roots.** Run the suggester from `threading.md` on every unit that still has no link.
9. **Embeddings.** Queue background embedding of `content_text` for `is:similar`. Import returns before this finishes.
10. **Memories.** Queue memory extraction (`memories.md`). Also runs in the background.

Return the import summary right after step 8:

```json
{ "source_id": 3, "messages": 1284, "new_authors": 2, "conversations": 41,
  "links": { "discord": 212, "suggested": 380, "root": 97, "unattached": 55 } }
```

## Blocks

Walk each channel's messages in `(ts, id)` order. Start a new block when any of these is true:

- the author changes
- more than `block_gap_ms` (default 5 min) passed since the previous message
- the message is a native reply (`reply_to_id` set) and the previous message in the block is not a reply to the same target
- a conversation split falls between the two messages

Excluded messages do not break a block. They stay inside it and are skipped when building training text.

Link edits never change block membership. To answer two people from one block, the user splits a line instead. The one exception is a conversation "Split here" that falls inside a block: cut the block in two at that point and copy the block's link to the new second block.

## Conversations

A conversation is a bookmark into the continuous feed.

1. Walk each channel's messages in `(ts, id)` order.
2. Start a new conversation when the gap since the previous message exceeds `split_gap_ms` (default 30 min).
3. Write one `conversations` row per run.

Re-importing an overlapping file can extend a conversation. If the extended conversation was approved, clear `approved_at`, because its content changed.

User edits:

- **Split here** (between two messages): end the current conversation at the earlier message and start a new one at the later. Both get `manual = 1`.
- **Merge with above**: join two neighbouring conversations into one with `manual = 1`.

Splitting or merging clears `approved_at` on every resulting conversation. The user re-approves what they still want.

Splits never touch links. An arrow can point across a split; see `threading.md`.
