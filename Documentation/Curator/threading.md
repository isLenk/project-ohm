# Threading

How reply links work, what the backend guesses, and which edits it accepts.

## The model in one paragraph

Every **unit** (a block, or a split line inside a block) points at the one earlier message it answers, or is marked as a thread start, or is unattached. The UI draws each link as an arrow from the unit to its parent. A message's effective parent is its own link if it is a split line, otherwise its block's link. Threads are the trees those links form. They ignore conversation boundaries.

## Link states

| State | Stored as | Drawn as | Exported? |
|---|---|---|---|
| Accepted, from Discord | `status='accepted', origin='discord'` | solid arrow | yes |
| Accepted, by the user | `status='accepted', origin='manual'` | solid arrow | yes |
| Suggested | `status='suggested', origin='suggested'` | dashed arrow with ✓ / ✕ | only if accepted, or accepted on approve (see README, open question 2) |
| Thread start | `status='root'`, `parent_id NULL` | hollow dot, "thread start" label | yes, as a root with no context |
| Unattached | no row | amber hollow dot, "not attached" | no |

The arrow color and gutter lane come from the replying author's `color_index`. The backend only has to return the index. Lane geometry is a UI concern.

## User operations

Each operation is one API call (see `api.md`) and one transaction. Each returns the changed units so the UI can redraw, plus an `inverse` operation the UI keeps on its undo stack for Ctrl+Z.

| Operation | Effect |
|---|---|
| **Drag a block's dot onto message P** | Upsert the block's link: `parent_id=P, status='accepted', origin='manual'`. Split lines inside the block keep their own links. |
| **Drag a line's dot onto message P** | Upsert a message-level link for that line. It becomes a split line. |
| **Drag a split line back onto its own block** | Delete the message-level link. The line follows its block again. |
| **Click an arrow** | Delete that link. A block becomes unattached. A split line has no unattached state, so it rejoins its block and follows the block's arrow again. If the deleted link was suggested, also insert into `rejected_suggestions`. |
| **✓ on a suggested arrow** | `status='accepted'`, origin stays `suggested` so we can measure suggester precision. |
| **✕ on a suggested arrow** | Delete the link and record it in `rejected_suggestions`. Run the suggester again for that unit, which may produce the next-best candidate or leave it unattached. |
| **Mark as thread start** | Upsert `status='root'`. |
| **Exclude message** | `messages.excluded=1, exclude_reason='manual'`. Links pointing *to* it are kept. Training context skips over it (see `training.md`). |

### Validation

Reject with `422` and one of these codes. The UI also prevents most of them while dragging, but the server is the source of truth.

| Code | Rule |
|---|---|
| `parent_not_earlier` | Parent must be strictly earlier than the unit's first message, ordered by `(ts, id)`. |
| `parent_in_unit` | A block cannot point at one of its own messages. A line cannot point at itself. |
| `cycle` | Cannot happen if `parent_not_earlier` holds. Keep the check anyway as a guard. |
| `parent_missing` | Parent message id is not in the project. |
| `different_channel` | Parent must be in the same channel. |

Any accepted change that touches a unit inside an approved conversation clears that conversation's `approved_at` and returns it in `conversations_changed`.

## The suggester

Runs on import, and again for a unit after its suggestion is rejected. It must be deterministic for a given project state so tests are stable.

### Candidates

For a unit `u` starting at message `m`:

- earlier messages in the same channel
- at most 50 messages back, and at most 6 hours back
- not in `u` itself
- not in `rejected_suggestions` for `u`
- not excluded

### Score

Score each candidate `c` in `[0, 1]`. Start with these weights and tune them against labelled data (below).

| Signal | Weight | How |
|---|---|---|
| `m` mentions `c`'s author | 0.35 | `c.author_id in m.mentions`, or the author's name appears as a word |
| `c` mentions `m`'s author | 0.20 | someone was pinged and is now answering |
| `c` looks like a question | 0.10 | ends in `?`, or starts with a question word |
| Shared content | 0.20 | cosine similarity of embeddings, clamped to `[0,1]`; token Jaccard if embeddings are not ready yet |
| Recency | 0.15 | `exp(-Δmessages / 8)` |
| Turn-taking | +0.10 bonus | `c`'s author spoke right before `m`'s author's previous block (A→B→A) |

Take the highest-scoring candidate. Then:

- score ≥ `suggest_high` (0.6): write a `suggested` link
- score < `suggest_low` (0.3), **and** `m` is the first unit after a conversation split or after a 10-minute silence: write `root`
- anything else: leave unattached

Mass-ping messages (3+ mentions, no reply reference) usually start something new, but they are also the most ambiguous. Leave them unattached unless the score is high.

Optionally, a later version can ask the local LLM (`llama-server` on `:5000`) to pick among the top 3 candidates. It must stay optional and time-boxed, because import should not depend on the model being up.

### Measuring it

Every accepted link with `origin='suggested'` is a true positive. Every row in `rejected_suggestions` is a false positive. Expose both counts in `GET /stats/suggester` so the weights can be tuned against real curation. Rejections record the link's `origin`, so the same numbers for the model are under `"model"` in that response. Rejections from before project version 2 have no origin and count as the heuristic's.

## Approval

`POST /conversations/{id}/approve` does the following, in order:

1. Count units in the conversation that are suggested, and units that are unattached.
2. If the request does not include `"confirm": true` and either count is non-zero, return `409 needs_confirm` with both counts. The UI shows them in a confirm dialog.
3. With confirm: accept every remaining suggested link, keep unattached units as they are (they are skipped at export), set `approved_at = now`.

Archiving sets `archived_at`. An archived conversation is never exported, even if `approved_at` is set.

## Feed geometry the UI needs

The feed endpoint returns, per unit: its id, kind, first and last message id, author, `color_index`, and its link (`parent_id`, `status`). For links whose parent is outside the page that was returned, include `parent_ts` and `parent_preview` (first 60 characters). The UI then draws an arrow to the top or bottom edge, labelled with that time and text, and clicking it jumps there.
