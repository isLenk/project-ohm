### Ohm Lite
The voice side of Ohm needs my PC, but I don't want Ohm to disappear every time the PC is off. Ohm Lite is the small part of him that lives on the NAS and is always around.

It is the same Discord bot (same token), without the custom model, voice or memory. It only does something when given an explicit command.

It uses:
1. Discord slash commands
2. Codex CLI (through the [brain](../brain/README.md)) as the model
3. `ddgs` for web search

> It asks Discord for no events at all (`Intents.none()`), so it never sees a chat message. This is also why it can be signed in at the same time as the PC's Ohm without the two answering each other.

---
### Commands:
- `/itemize photo: paid_by: note:` Reads a photo of a receipt (or a bill, a shelf...) and lists the items with prices and totals. Everyone can then pick what they had.
- `/web query:` Searches the web and gives a short answer with the links it used.

Both work in the server and in a DM with Ohm, so a photo straight from my phone works too.

### The receipt
The reply to `/itemize` has two controls:
- **Pick my items**: opens a private checklist with my items already ticked. Tick to add, untick to remove, close the menu and it's saved.
- **Pick for someone else**: asks who, then opens the same checklist for them.

The message always shows who has each item and who owes the payer how much. Two people on the same item share it.

> Tax, tip and discounts are spread in proportion to what each person had (it's the gap between the items and the receipt's total).

> The first version had five controls (claim, assign, clear, split the rest, done/reopen) and it wasn't obvious how to unassign something. One checklist does all of it.

---
### How to Deploy
1. Execute `apps/lite/deploy.sh`

That's it. It copies this folder and `apps/brain/brain` to `/volume2/docker/ohm_lite` on the NAS and rebuilds the container. The `.env` and `data/` on the NAS are left alone.

> The script refuses to deploy if a `.py` file doesn't parse. A syntax error leaves the bot restarting in a loop (found out the hard way).

### First time only
1. On the NAS, `cp .env.example .env` in `/volume2/docker/ohm_lite/lite` and fill it in
2. Sign Codex in: `ssh -t nas docker exec -it ohm-lite codex login --device-auth`
3. Open the link, enter the code
4. Check with `ssh nas docker exec ohm-lite codex login status`

> Same ChatGPT account as the PC, but its own sign-in (kept in `data/codex`). Copying the PC's sign-in file over risks the two machines signing each other out when it renews.

Logs: `ssh nas docker logs --tail 50 ohm-lite`. Model calls also show up in the app's Brain log.

---
### Files
- `bot.py` The Discord client, one `@tree.command` per command
- `itemize.py` Shrinks the photo and reads it through the brain
- `receipt.py` The receipt message: the controls, what each press does, and the sums
- `web.py` The search and the answer
- `names.py` Gives the brain the NAS's list of people so names get masked
- `deploy.sh` Copy to the NAS and rebuild

### Adding a command
1. Put the work in its own file like `itemize.py`: a prompt, a schema and a `brain.run(Task(...))`
2. Add a `@tree.command` in `bot.py` that calls it
3. Deploy

- Anything slower than 3 seconds needs `interaction.response.defer()` first, or Discord gives up on it.
- The model call blocks, so run it with `asyncio.to_thread`.
- Buttons and menus don't need a callback each. Give them a `custom_id` and handle the press in one function (see `receipt.handle()`).
- A new role needs its backends: `os.environ.setdefault("BRAIN_ROLE_<ROLE>", "codex,openrouter")`.

---
### Settings (`.env`)
- `DISCORD_TOKEN` Same token as the PC's Ohm
- `MEMORY_API_KEY` The memory service's key (for log rows and the names to mask)
- `LITE_USERS` Discord user ids allowed to use the commands, comma separated. Empty means anyone who can see the bot
- `OPENROUTER_API_KEY`, `BRAIN_OPENROUTER_MODEL` Optional. A vision model for when Codex doesn't answer

---
### Running on the NAS
UGREEN NASync DXP4800
- Intel N100 Quad-Core
- 8GB DDR5
- The container sits at around 40MB of RAM

> Every command spends my Codex subscription, so `LITE_USERS` is worth setting.

### Running it on the PC
```
cd apps/lite
../../.venv/Scripts/python.exe bot.py
```
Stop the NAS one first (`ssh nas docker stop ohm-lite`), otherwise both answer the same command.

---
### Privacy
- The photo goes to Codex as it is.
- Names in a `note` are swapped for stand-ins before the model sees them. If the NAS can't be reached, a command with a note fails instead of sending names out.
- A `/web` query goes to the search engines exactly as typed. The model gets it with names masked.

---
### Notes

### November 9 2026
- `/itemize` didn't show up in Discord after the first deploy. It was registered fine, the Discord app just needed a reload (Ctrl+R).
- Search engines are a lot flakier from the NAS than from the PC. Yahoo, Google and Bing answer (Yahoo most of the time). Yandex and DuckDuckGo time out, Brave, Mojeek and Startpage are always empty. So every engine is asked at once, and the whole thing is tried twice before giving up.
- `/web` only reads the result snippets, not the pages. Good for quick facts, not for anything that needs the full article.
- Receipts with more than 50 items can only have the first 50 picked (Discord allows 25 options per menu, and there are two menus).
