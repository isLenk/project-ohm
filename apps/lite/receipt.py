"""Shared receipt: who had what and who owes what.
Saved per message in data/receipts/ so controls survive restarts."""

import json
import os
from pathlib import Path

import discord

import itemize

DATA = Path(os.getenv("LITE_DATA") or Path(__file__).parent / "data") / "receipts"
MENU_ITEMS = 25      # Discord max per menu
MENUS = 2
EMBED_MAX = 4000     # Discord max is 4096
NOBODY = discord.AllowedMentions.none()  # mention without pinging


# ── Saving ──────────────────────────────────────────────────────────────────
def load(message_id):
    try:
        return json.loads((DATA / f"{message_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(message_id, state: dict):
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / f"{message_id}.json").write_text(json.dumps(state), encoding="utf-8")


def new(found: dict, owner: int, payer: int, channel: int) -> dict:
    return {"owner": str(owner), "payer": str(payer), "channel": channel, "found": found, "claims": {}}


# ── The sums ────────────────────────────────────────────────────────────────
def split(state: dict):
    """({user id: owed}, unclaimed). Shared items split evenly; tax/tip scaled proportionally"""
    items = state["found"].get("items") or []
    priced = sum(i.get("price") or 0 for i in items)
    total = state["found"].get("total")
    scale = total / priced if total and priced > 0 else 1
    owes, unclaimed = {}, 0.0
    for n, item in enumerate(items):
        amount = (item.get("price") or 0) * scale
        people = state["claims"].get(str(n)) or []
        if not people:
            unclaimed += amount
        for person in people:
            owes[person] = owes.get(person, 0) + amount / len(people)
    return {person: round(amount, 2) for person, amount in owes.items()}, round(unclaimed, 2)


# ── What it looks like ──────────────────────────────────────────────────────
def embed(state: dict) -> discord.Embed:
    found = state["found"]
    money = lambda n: f"{found.get('currency') or ''}{n:,.2f}"
    lines = []
    for n, item in enumerate(found.get("items") or []):
        people = state["claims"].get(str(n)) or []
        who = " ".join(f"<@{p}>" for p in people) or "*nobody yet*"
        price = f" · {money(item['price'])}" if item.get("price") is not None else ""
        lines.append(f"`{n + 1:>2}` {item_name(item)}{price} · {who}")
    # Trim to fit the embed limit
    shown = list(lines)
    while len("\n".join(shown)) > EMBED_MAX - 800:
        shown.pop()
    if len(shown) < len(lines):
        shown.append(f"... and {len(lines) - len(shown)} more")

    totals = " · ".join(f"{label} {money(found[key])}" for key, label in
                        (("subtotal", "Subtotal"), ("tax", "Tax"), ("total", "Total")) if found.get(key) is not None)
    owes, unclaimed = split(state)
    payer = state["payer"]
    people = [f"<@{p}> owes <@{payer}> **{money(a)}**" for p, a in owes.items() if p != payer]
    if payer in owes:
        people.append(f"<@{payer}> paid, and had {money(owes[payer])}")
    if unclaimed > 0:
        people.append(f"Not picked yet: {money(unclaimed)}")

    parts = ["\n".join(shown), totals, "\n".join(people), found.get("note") or "", itemize.mismatch(found, money)]
    out = discord.Embed(title=" · ".join(x for x in (found.get("title"), found.get("date")) if x) or "Receipt",
                        description="\n\n".join(p for p in parts if p))
    out.set_footer(text="Tax and extras are spread in proportion to what each person had.")
    out.add_field(name="Paid by", value=f"<@{payer}>")
    return out


def item_name(item: dict) -> str:
    qty = item.get("qty") or 1
    return (f"{qty:g}x " if qty != 1 else "") + str(item.get("name") or "?")


def checklist(state: dict, message_id, person: str) -> discord.ui.View:
    """Item menus with `person`'s items pre-ticked"""
    found = state["found"]
    items = list(enumerate(found.get("items") or []))[:MENU_ITEMS * MENUS]
    out = discord.ui.View(timeout=None)
    for k in range(0, len(items), MENU_ITEMS):
        chunk = items[k:k + MENU_ITEMS]
        options = [discord.SelectOption(
            label=f"{n + 1}. {item_name(item)}"[:100], value=str(n),
            description=f"{found.get('currency') or ''}{item['price']:,.2f}" if item.get("price") is not None else None,
            default=person in (state["claims"].get(str(n)) or []))
            for n, item in chunk]
        more = f" ({chunk[0][0] + 1}-{chunk[-1][0] + 1})" if len(items) > MENU_ITEMS else ""
        # min_values=0 lets someone untick everything
        out.add_item(discord.ui.Select(custom_id=f"rc:set:{message_id}:{person}:{k // MENU_ITEMS}",
                                       placeholder="Tick the items" + more, min_values=0, max_values=len(chunk),
                                       options=options))
    return out


def view(state: dict) -> discord.ui.View:
    """Receipt controls, handled by handle()"""
    out = discord.ui.View(timeout=None)
    out.add_item(discord.ui.Button(label="Pick my items", custom_id="rc:mine", style=discord.ButtonStyle.primary))
    out.add_item(discord.ui.UserSelect(custom_id="rc:who", placeholder="Pick for someone else", max_values=1))
    return out


# ── Presses ─────────────────────────────────────────────────────────────────
async def handle(client: discord.Client, interaction: discord.Interaction):
    """Handle a receipt or checklist press"""
    parts = (interaction.data.get("custom_id") or "").split(":")
    if parts[0] != "rc":
        return
    action, values = parts[1], interaction.data.get("values") or []
    # ? Checklists are separate messages, so the receipt id is in custom_id
    message_id = parts[2] if action == "set" else interaction.message.id
    state = load(message_id)
    if state is None:
        return await interaction.response.send_message("I've lost track of that receipt. Run /itemize again.",
                                                       ephemeral=True)

    if action in ("mine", "who") and (action == "mine" or values):
        person = str(interaction.user.id) if action == "mine" else values[0]
        whose = "you" if person == str(interaction.user.id) else f"<@{person}>"
        return await interaction.response.send_message(
            f"Tick everything {whose} had, then close the menu. Untick to take something off.",
            view=checklist(state, message_id, person), ephemeral=True, allowed_mentions=NOBODY)

    if action == "set":
        person, menu = parts[3], int(parts[4])
        # Replace this person's claims within this menu
        count = len(state["found"].get("items") or [])
        for n in range(menu * MENU_ITEMS, min((menu + 1) * MENU_ITEMS, count)):
            people = [p for p in state["claims"].get(str(n), []) if p != person]
            state["claims"][str(n)] = people + [person] if str(n) in values else people
        save(message_id, state)
        await interaction.response.edit_message(content="Saved. Change it here any time.",
                                                view=checklist(state, message_id, person))
        try:
            receipt = client.get_partial_messageable(state["channel"]).get_partial_message(int(message_id))
            await receipt.edit(embed=embed(state), view=view(state), allowed_mentions=NOBODY)
        except discord.HTTPException as e:
            print(f"[lite] Couldn't redraw receipt {message_id}: {e}")
            await interaction.followup.send("Saved, but I couldn't update the receipt message here.", ephemeral=True)
        return

    # Stale controls: redraw
    await interaction.response.edit_message(embed=embed(state), view=view(state), allowed_mentions=NOBODY)
