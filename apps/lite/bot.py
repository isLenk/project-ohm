"""Ohm Lite: slash commands with a cloud LLM brain, run from the NAS.
No intents, so it can share a token with the PC's Ohm."""

import asyncio
import io
import os
import sys

import discord
from discord import app_commands

import itemize as itemizer
import names  # noqa: F401  (registers names to mask)
import receipt
import web

# Empty = anyone
ALLOWED = {int(x) for x in os.getenv("LITE_USERS", "").replace(",", " ").split()}

client = discord.Client(intents=discord.Intents.none())
tree = app_commands.CommandTree(client)


async def refused(interaction: discord.Interaction) -> bool:
    """Reply and return True if not in LITE_USERS"""
    if ALLOWED and interaction.user.id not in ALLOWED:
        await interaction.response.send_message("You're not on my list for this one.", ephemeral=True)
        return True
    return False


@client.event
async def setup_hook():
    commands = await tree.sync()
    print(f"[lite] Commands: {', '.join('/' + c.name for c in commands)}")


@client.event
async def on_ready():
    print(f"[lite] Signed in as {client.user}")


@tree.command(name="itemize", description="List what's in a photo: a receipt, a bill, a shelf")
@app_commands.describe(photo="The photo to read", paid_by="Who paid (you, if left empty)",
                       note="Anything to add, like 'what did the drinks cost?'")
async def itemize(interaction: discord.Interaction, photo: discord.Attachment, paid_by: discord.User = None,
                  note: str = ""):
    if await refused(interaction):
        return
    if not (photo.content_type or "").startswith("image/"):
        return await interaction.response.send_message("That isn't a picture.", ephemeral=True)

    # ? Must answer within 3s; defer buys 15 min
    await interaction.response.defer()
    try:
        jpeg = itemizer.shrink(await photo.read())
    except Exception as e:
        print(f"[lite] Couldn't open {photo.filename}: {e}")
        return await interaction.followup.send("I couldn't open that picture.")

    found = await asyncio.to_thread(itemizer.read, jpeg, note)
    if found is None:
        return await interaction.followup.send("I couldn't read that right now. Try again in a bit.")
    if not found.get("items"):
        return await interaction.followup.send(found.get("note") or "I couldn't find anything to list in that photo.")

    state = receipt.new(found, owner=interaction.user.id, payer=(paid_by or interaction.user).id,
                        channel=interaction.channel_id)
    # ? Slash command attachments aren't shown, so repost the photo
    message = await interaction.followup.send(embed=receipt.embed(state), view=receipt.view(state),
                                              file=discord.File(io.BytesIO(jpeg), "photo.jpg"),
                                              allowed_mentions=receipt.NOBODY)
    receipt.save(message.id, state)


@tree.command(name="web", description="Look something up on the web and get a short answer with links")
@app_commands.describe(query="What to look up")
async def web_search(interaction: discord.Interaction, query: str):
    if await refused(interaction):
        return
    await interaction.response.defer()
    text = await asyncio.to_thread(web.run, query)
    await interaction.followup.send(text, allowed_mentions=receipt.NOBODY)


@client.event
async def on_interaction(interaction: discord.Interaction):
    """Route menus and buttons to receipt"""
    if interaction.type is discord.InteractionType.component:
        await receipt.handle(client, interaction)


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        sys.exit("DISCORD_TOKEN is not set")
    if os.getenv("CODEX_HOME"):
        os.makedirs(os.environ["CODEX_HOME"], exist_ok=True)  # Codex won't start without it
    client.run(token)
