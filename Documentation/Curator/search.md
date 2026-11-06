# Search (Ctrl+K)

One input searches messages, people and conversations, and also runs actions. Results come back grouped. Picking a message calls `GET /feed?around=<id>`, so the UI jumps to it in the continuous feed.

## Query grammar

The query is split into tokens. Operators are pulled out first. Whatever is left is the search text.

| Token | Meaning |
|---|---|
| `@name` or `from:name` | author filter. Matches `name` or `display_name`, case-insensitive prefix. The UI turns it into a chip. Can repeat (OR). |
| `/pattern/flags` | regex on `content_text`. Same as switching the mode toggle to Regex. |
| `is:similar` | only messages that have a near-duplicate (cosine ≥ 0.8) elsewhere |
| `is:unattached` | only units with no link and no root mark |
| `is:suggested` | only units whose link is a suggestion |
| `is:excluded` | include excluded messages (hidden by default) |
| `in:approved` / `in:archived` | restrict to conversations in that state |
| `before:YYYY-MM-DD` / `after:YYYY-MM-DD` | time range, in the user's local timezone |
| `"exact phrase"` | phrase match |

Unknown `word:value` tokens are treated as plain text.

## Execution

1. **Filters first:** apply author, time, state and `is:` filters as SQL `WHERE` clauses on `messages` joined with `links` and `conversations`.
2. **Text mode:** search `messages_fts` with FTS5. Quote each term and add a prefix `*` to the last one, so typing feels live. Rank with `bm25`.
3. **Regex mode:** compile with Python `re` and a 200 ms timeout (run it in a worker and cancel on timeout). Scan the filtered rows, newest first, and stop at `limit`. Return `422 bad_regex` with the compiler's message if it does not compile.
4. **Highlights:** return match spans as `[start, end]` offsets into `content_text`. The UI underlines them. It never re-runs the match itself.

## "Similar wording, other people"

When the query has text, also return up to 5 messages from *other* authors whose embedding is close to the best match (cosine ≥ 0.6). This is how a search for `from:KaptainDoosh baro` also surfaces "Baro could hit". Skip this group if embeddings are still being built.

## Response

```json
{
  "messages": [
    { "id": "...", "author_id": "...", "ts": 0, "content_text": "I’m down to baro",
      "highlights": [[12, 16]], "conversation_id": 12 }
  ],
  "similar": [ { "id": "...", "author_id": "...", "ts": 0, "content_text": "Baro could hit", "score": 0.71 } ],
  "people": [ { "author_id": "...", "messages": 5, "share": 0.45, "memories": 4 } ],
  "conversations": [ { "id": 12, "first_ts": 0, "last_ts": 0 } ],
  "actions": [ { "id": "train", "label": "Train LoRA on approved conversations" } ]
}
```

Actions are a static list filtered by the text, for example "train", "approve current", "save" and "export". The UI owns what each action does.

## Performance target

Text queries under 50 ms on 100k messages. Debounce in the UI at 120 ms and cancel the previous request when a new one starts.
