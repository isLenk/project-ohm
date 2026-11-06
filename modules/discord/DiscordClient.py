# February 1 2025
# Description: Discord module for the bot


import threading
import time
from collections import deque

import discord
from controller.model import ModelObject
from modules.discord.DiscordVoice import DiscordVoice
from controller.memory import memory, HISTORY_LIMIT, HISTORY_MINUTES
testing_channel = 1335351030308802630
VOICE_MAX_TOKENS = 120  # ~20s of speech at most per voice reply
voice_channel = 1335374964445941812
import utils.AudioFix as AudioFix
class DiscordClient(discord.Client):
    possible_intents = ["join", "leave"]
    model: ModelObject
    logging_channel: int
    voice: DiscordVoice
    
    def add_task(self, func, *args, **kwargs):
        """Add a task to the event loop"""
        return self.loop.create_task(func(*args, **kwargs))

    @staticmethod
    def initialize(model: ModelObject, testing_channel=testing_channel):
        """Called by main.py to initialize the Discord client
        """


        # Establish permissions for the bot
        intents = discord.Intents.all()
        intents.message_content = True
        
        client = DiscordClient(intents=intents)

        client.testing_channel = testing_channel
        client.model = model
        client.voice = DiscordVoice(client, client)

        client.logging_channel = testing_channel

        return client

    async def on_ready(self):
        print(f'Logged on as {self.user}!')
        await self.join_testing_channel(vc=True)
    
    @property
    def character(self) -> str:
        return self.model.name.lower()

    @staticmethod
    def speaker_name(user) -> str:
        return getattr(user, "display_name", None) or str(user)

    # ── Conversation ────────────────────────────────────────────────────────
    # Each channel's recent conversation is kept here so Ohm can follow along turn to turn.
    # The NAS keeps the permanent copy and is only read to pick up where we left off after a restart.

    def conversation(self, channel_id) -> deque:
        if not hasattr(self, "_conversations"):
            self._conversations = {}
        if channel_id not in self._conversations:
            self._conversations[channel_id] = deque(memory.history(self.character, channel_id), maxlen=HISTORY_LIMIT)
        return self._conversations[channel_id]

    def history(self, channel_id) -> list[dict]:
        """The last HISTORY_MINUTES of conversation in a channel, oldest first."""
        cutoff = time.time() - HISTORY_MINUTES * 60
        return [
            {"role": m["role"], "content": m["content"]}
            for m in self.conversation(channel_id)
            if m["content"] and m["time"] >= cutoff
        ]

    def make_stream_response(self, said_by, channel):
        """Start a voice reply to what people said. said_by: {discord user: what they said}."""
        turn = Turn(self, channel, "voice", said_by)
        memories = memory.relevant_memories(self.character, " ".join(said_by.values()))
        if memories:
            print(f"[memory] recalled: {[m['content'] for m in memories]}")
        turn.prompt(turn.line, memories)
        return turn

    def make_idle_stream(self, channel):
        """Start something Ohm says on his own when the call has gone quiet."""
        turn = Turn(self, channel, "voice")
        present = [self.speaker_name(m) for m in getattr(channel, "members", []) if not m.bot]
        turn.prompt(
            "(Nobody has said anything for a bit. Say one short thing to keep the hangout going: "
            "follow up on what you were just talking about, ask someone something, or bring up "
            "something random. Just say the line, nothing else."
            + (f" In the call right now: {', '.join(present)}." if present else "") + ")"
        )
        return turn

    async def join_testing_channel(self, vc=False):
        channel_id = [testing_channel, voice_channel][bool(vc)]
        channel = self.get_channel(channel_id)
        print(channel)
        print("Joining channel...")

        vc = await self.voice.join_channel(channel)
        print("Joined channel")
        return vc
    
    def log(self, message):
        channel = self.get_channel(self.logging_channel)
        channel.send(message)

    def get_channel(self, id):
        return super().get_channel(id)

    def make_response(self, message):
        turn = Turn(self, message.channel, "text", {message.author: message.content})
        memories = memory.relevant_memories(self.character, message.content)
        response, contains_intent = self.model.generate_text(
            turn.line, related_memories=memories, history=self.history(turn.channel_id),
        )

        # If response contains it's name at the start, remove it
        if response.startswith(self.model.name + ":"):
            response = response[len(self.model.name)+1:].lstrip()

        turn.reply = response
        turn.commit()
        return response, contains_intent
    
    async def on_message(self, message, source="message"):
        if source == "message":
            print(f'Message from {message.author}: {message.content}')
            if message.channel.id != self.testing_channel:
                return
            if message.author == self.user:
                return
        elif source == "voice":
            return
        
        response, contains_intent = self.make_response(message)

        await message.reply(response)
        
    async def parse_intent(self, message, response, source):
        intent = self.model.get_intent(response, self.possible_intents)

        channel = "None" if source == "voice" else message.channel
        match intent:
            case "join":
                if source == "voice": return
                print("Joining voice channel")
                # Ensure the author is in a voice channel
                # Ensure that the channel is allowed to be joined
                if message.author.voice is None or message.author.voice.channel is None:
                    await channel.send("You need to be in a voice channel to use this command.")
                    return
                
                if not message.author.voice.channel.permissions_for(message.guild.me).connect:
                    await channel.send("I don't have permission to join your voice channel.")
                    return
                
                # Ensure channel id is correct
                if message.author.voice.channel.id != voice_channel:
                    await channel.send("No. I can't join that channel.")
                    return
                
                # Join the voice channel
                await self.voice.join_channel(message.author.voice.channel)

            case "leave":
                if source == "voice": return
                await self.voice.leave_channel(message)
                print("Leaving voice channel")
            case _:
                print(f'Intent: {intent}')


class Turn:
    """One exchange in a conversation: what people said, then Ohm's reply.

    Call prompt() to open the LLM stream, then iterate it for the reply. Nothing is saved until commit(), so a reply that
    gets thrown away before Ohm says it (discard()) never ends up in the conversation or memory.
    If he gets cut off partway, commit() saves what he'd said up to then.
    """

    def __init__(self, client: DiscordClient, channel, source: str, said_by: dict | None = None):
        self.client = client
        self.source = source
        self.said_by = said_by or {}
        self.channel_id = getattr(channel, "id", None)
        self.guild_id = getattr(getattr(channel, "guild", None), "id", None)
        self.line = "\n".join(f"{client.speaker_name(u)}: {t}" for u, t in self.said_by.items())
        self.reply = ""
        self._stream = None
        self._chunks = iter(())
        self._finished = False
        self._lock = threading.Lock()

    def prompt(self, text, memories=None):
        """Open the LLM stream (with the conversation so far as context)."""
        # ? In voice, a hard cap so a "how do I..." question can't turn into a minute-long lecture
        limit = {"max_tokens": VOICE_MAX_TOKENS} if self.source == "voice" else {}
        self._stream = self.client.model.generate_stream_text(
            text, related_memories=memories, history=self.client.history(self.channel_id), **limit,
        )
        self._chunks = iter(self._stream)

    def __iter__(self):
        return self

    def __next__(self):
        if self._finished:
            raise StopIteration
        chunk = next(self._chunks)
        if chunk.choices and chunk.choices[0].delta.content:
            self.reply += chunk.choices[0].delta.content
        return chunk

    def _stop(self):
        """Mark the turn finished and drop the HTTP connection so the LLM stops generating."""
        with self._lock:
            if self._finished:
                return False
            self._finished = True
        if hasattr(self._stream, "close"):
            try:
                self._stream.close()
            except Exception:
                pass
        return True

    def discard(self):
        self._stop()

    def commit(self):
        """Save this exchange to the conversation, the NAS, and long-term memory (once)."""
        if not self._stop():
            return
        client, now = self.client, time.time()
        convo = client.conversation(self.channel_id)

        for user, text in self.said_by.items():
            user_id = getattr(user, "id", None)
            content = f"{client.speaker_name(user)}: {text}"
            convo.append({"role": "user", "content": content, "time": now})
            memory.log_message_async(
                client.character, "user", content, user_id=user_id,
                source=self.source, guild_id=self.guild_id, channel_id=self.channel_id,
            )
            memory.remember_if_relevant_async(
                client.model, client.character, client.speaker_name(user), text,
                user_ids=[user_id] if user_id else None,
            )

        reply = self.reply.strip()
        if reply:
            convo.append({"role": "assistant", "content": reply, "time": now})
            memory.log_message_async(
                client.character, "assistant", reply,
                source=self.source, guild_id=self.guild_id, channel_id=self.channel_id,
            )