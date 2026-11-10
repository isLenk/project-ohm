# February 1 2025
# Description: Discord module for the bot


import asyncio
import threading
import time
from collections import deque

import discord
from controller.model import ModelObject
from modules.discord.DiscordVoice import DiscordVoice
from modules.vision import screen
from controller import addressee, brief, modules
from controller.memory import memory, person_names, HISTORY_LIMIT, HISTORY_MINUTES
from controller.wrappers.openai import NO_CHECKS
from utils import WebSearch, latency, trace
from config.constants import strip_voice_tags
from utils.Vision import message_images, picture_note
from utils.VoiceTags import VoiceTagFilter
from utils.ReplyCleanup import (AtFilter, clean_reply, cut_transcript, outgoing_mentions, readable_mentions,
                                speaker_labels, stop_sequences, strip_leading_label)
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
        self.add_task(self.name_unnamed_people)
        # A voice call needs both ears and a mouth
        off = [name for name in ("stt", "tts") if not modules.enabled(name)]
        if off:
            print(f"[voice] Not joining voice: {', '.join(off)} turned off in the controller.")
            return
        # Get the TTS speaking as this character before anyone talks to them
        self.add_task(self.voice.tts_module.preload_voice)
        await self.join_testing_channel(vc=True)
    
    async def name_unnamed_people(self):
        """Send names, Discord handles and avatars for people the NAS only knows by id, or is missing one of
        those for (logged before they were sent along)."""
        people = await asyncio.to_thread(memory.people, self.character)
        for p in people:
            if (p["name"] != p["user_id"] and p.get("username") and p.get("avatar_url")) or not p["user_id"].isdigit():
                continue
            uid = int(p["user_id"])
            # A server member's display name is what everyone sees in chat, so prefer it
            user = next((m for g in self.guilds if (m := g.get_member(uid))), None) or self.get_user(uid)
            if user is None:
                try:
                    user = await self.fetch_user(uid)
                except discord.HTTPException:
                    continue
            await asyncio.to_thread(memory.set_name, uid, self.speaker_name(user), getattr(user, "name", None),
                                    self.avatar_url(user))

    @property
    def character(self) -> str:
        return self.model.slug

    @staticmethod
    def speaker_name(user) -> str:
        return getattr(user, "display_name", None) or str(user)

    @staticmethod
    def avatar_url(user) -> str | None:
        """What Discord shows for them: their server avatar if they set one, else theirs, else the default."""
        avatar = getattr(user, "display_avatar", None)
        return str(avatar.url) if avatar else None

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
        recent = [
            {"role": m["role"], "content": m["content"]}
            for m in self.conversation(channel_id)
            if m["content"] and m["time"] >= cutoff
        ]
        # ? A reply saved as "Lenk: ..." (before that was cleaned up) teaches him to keep starting replies that way
        labels = speaker_labels(recent, [self.model.name])
        for m in recent:
            if m["role"] == "assistant":
                m["content"] = strip_leading_label(m["content"], labels)
        return [m for m in recent if m["content"]]

    def make_stream_response(self, said_by, channel, on_search=None):
        """Start a voice reply to what people said. said_by: {discord user: what they said}.
        on_search(): called when the reply has to wait on a web search."""
        turn = Turn(self, channel, "voice", said_by)
        said = " ".join(said_by.values())
        memories = recall(self.character, said)
        people = self.people_in(said)
        # One call: search? screen? who for?
        checks = traced_checks(self, turn.line, self.history(turn.channel_id))
        latency.mark("pre_checks")
        if checks["search"] and on_search and not WebSearch.answered(checks["search"]):
            on_search()
        shot, seen = self.look_at_screen(said, checks)
        turn.prompt(turn.line, memories, people, screen=shot, seen=seen, checks=checks)
        return turn

    def look_at_screen(self, said, checks=None) -> tuple[str | None, str]:
        """(a screenshot, the line saying what's on it) if the vision module is on and `said` is about the screen,
        else (None, ""). checks: the reply's pre_checks(), so the screen question isn't asked a second time."""
        if not screen.on():
            trace.stage("screen", "skipped", "vision off")
            return None, ""
        if not said.strip() or not (checks or self.model.pre_checks(said))["screen"]:
            trace.stage("screen", "skipped", "not about the screen")
            return None, ""
        trace.stage("screen", "running", "looking")
        shot, seen = screen.look()
        if shot:
            print(f"[vision] Looking at the screen. {seen}")
        trace.stage("screen", "done" if shot else "failed", seen or "no screenshot", log=seen or None)
        return shot, seen

    def make_idle_stream(self, channel, about_screen=False, region=None, on_search=None):
        """Start something Ohm says on his own when the call has gone quiet.
        about_screen: something new came up on the screen (modules/vision/watch.py), so talk about that.
        region: where on the screen it came up, so the screenshot shows just that (watch.changed_region).
        on_search: unused, matches make_stream_response."""
        turn = Turn(self, channel, "voice")
        present = [self.speaker_name(m) for m in getattr(channel, "members", []) if not m.bot]
        shot, seen = screen.look(region)  # (None, "") unless the vision module is on
        trace.stage("screen", "done" if shot else "skipped", seen if shot else "vision off", log=seen or None)
        for name in ("memory", "checks", "search"):
            trace.stage(name, "skipped", "nobody asked anything")
        idea = ("comment on what's on the screen you can see" if shot and about_screen else
                "comment on what's on the screen you can see, follow up on what you were just talking about, "
                "or ask someone something" if shot else
                "follow up on what you were just talking about, ask someone something, or bring up something random")
        turn.prompt(
            f"(Nobody has said anything for a bit. Say one short thing to keep the hangout going: {idea}. "
            "Just say the line, nothing else."
            + (f" In the call right now: {', '.join(present)}." if present else "") + ")",
            screen=shot, seen=seen, checks=NO_CHECKS,  # ? nobody asked anything, so there's nothing to look up
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

    def incoming_text(self, message) -> str:
        """What someone wrote, with mentions as "@Name" like the training data (and the bot as "@Ohm")."""
        users = {u.id: self.speaker_name(u) for u in message.mentions}
        if self.user is not None:
            users[self.user.id] = self.model.name
        roles = {r.id: r.name for r in message.role_mentions}
        channels = {c.id: c.name for c in message.channel_mentions if getattr(c, "name", None)}
        return readable_mentions(message.content, users, roles, channels)

    RECENT_LINES = 8  # history pre_checks reads for "to"

    def known_people(self, history) -> dict:
        """{display name: every name they go by} from the NAS and the history"""
        people = {}
        for u in memory.users():
            names = person_names(u)
            if names:
                people[u.get("display_name") or names[0]] = names
        for label in speaker_labels(history, []):
            people.setdefault(label, [label])
        return people

    def checks_for(self, line, history) -> dict:
        """pre_checks() with the recent chat"""
        parsed = addressee.split(line)
        if not parsed:
            return self.model.pre_checks("")
        speaker = parsed[-1][0]
        text = " ".join(t for s, t in parsed if s == speaker)
        recent = []
        for m in history[-self.RECENT_LINES:]:
            recent.append(m["content"] if m["role"] == "user" else f"{self.model.name}: {m['content']}")
        recent += [f"{s}: {t}" for s, t in parsed if s != speaker]
        names = [s for s, _ in addressee._lines(history[-self.RECENT_LINES:], self.model.name)] + [s for s, _ in parsed]
        known = [n for all_names in self.known_people(history).values() for n in all_names]
        return self.model.pre_checks(text, "\n".join(recent)[-1500:], speaker, names, known)

    def aside(self, line, history, checks=None) -> str:
        """The "not for you" note for this turn, or "" (controller/addressee.py)"""
        return addressee.aside(line, history, self.model.name, self.known_people(history), (checks or {}).get("to"))

    def people_in(self, text, mentioned=()):
        """People `text` names (or @mentions), with what's remembered about each; see memory.people_in."""
        bot = getattr(self.user, "id", None)
        mentioned = [u for u in mentioned if u.id != bot]
        for u in mentioned:  # so someone who's only been talked about still has a name to be found by
            memory.set_name_async(u.id, self.speaker_name(u), getattr(u, "name", None), self.avatar_url(u))
        people = memory.people_in(self.character, text, {u.id: self.speaker_name(u) for u in mentioned})
        if people:
            print(f"[memory] people: {[(p['name'], len(p['notes'])) for p in people]}")
        return people

    def make_response(self, message, images=None):
        """Text reply to `message`: (what to send to Discord, contains_intent). Empty if there's nothing to say.
        images: the pictures attached to it, as data URLs (utils/Vision.message_images)."""
        said = picture_note(self.incoming_text(message), len(images or []))
        trace.begin("text", {self.speaker_name(message.author): said}, self.character,
                    note=f"#{getattr(message.channel, 'name', 'chat')}")
        turn = Turn(self, message.channel, "text", {message.author: said})
        memories = recall(self.character, said)
        people = self.people_in(said, message.mentions)
        history = self.history(turn.channel_id)
        checks = traced_checks(self, turn.line, history)
        shot, seen = self.look_at_screen(self.incoming_text(message), checks)
        trace.stage("prompt", "done", f"{len(history)} lines of history", turn=turn.trace_id)
        trace.stage("llm", "running", str(getattr(self.model.model, "model", "")), turn=turn.trace_id)
        try:
            response, contains_intent = self.model.generate_text(
                turn.line, related_memories=memories, history=history, stop=turn.labels(history), people=people,
                images=images or None, screen=shot, seen=seen, checks=checks, summary=brief.current(turn.channel_id),
                aside=self.aside(turn.line, history, checks),
            )
        except Exception as e:
            trace.stage("llm", "failed", str(e)[:60], log=repr(e), turn=turn.trace_id)
            trace.end("failed", repr(e), turn=turn.trace_id)
            raise
        trace.stage("llm", "done", f"{len(response)} chars", turn=turn.trace_id)
        trace.reply(response, turn=turn.trace_id)

        # One reply: no leading "Ohm:", and nothing past where he starts writing other people's lines
        response = clean_reply(response, self.model.name, turn.speakers)
        # Voice replies in the history can teach him tags; they'd show up literally in text chat
        response = strip_voice_tags(response)
        # "@Lenk" -> a real ping; the history keeps "@Lenk", the form the model knows
        members = [m for m in getattr(message.guild, "members", None) or [] if m.id != getattr(self.user, "id", None)]
        response, turn.reply = outgoing_mentions(response, members, reply_to=message.author)

        turn.commit()
        trace.end("done", turn=turn.trace_id)
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
        
        images = await message_images(message)
        if images:
            print(f"[vision] {len(images)} picture(s) attached")
        response, contains_intent = self.make_response(message, images)

        if not response:
            print("[discord] Nothing left of the reply after cleanup; not sending it.")
            return
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


# Traced reply steps (utils/trace.py)

def recall(character: str, said: str) -> list[dict]:
    trace.stage("memory", "running", "searching memories")
    started = time.perf_counter()
    memories = memory.relevant_memories(character, said)
    ms = (time.perf_counter() - started) * 1000
    if memories:
        print(f"[memory] recalled: {[m['content'] for m in memories]}")
    trace.stage("memory", "done", f"{len(memories)} hit{'s' if len(memories) != 1 else ''} · {ms:.0f} ms")
    for m in memories:
        trace.log("memory", m.get("content", ""))
    return memories


def traced_checks(client, line: str, history: list) -> dict:
    trace.stage("checks", "running", "search? screen? who for?")
    started = time.perf_counter()
    checks = client.checks_for(line, history)
    ms = (time.perf_counter() - started) * 1000
    trace.stage("checks", "done", f"{ms:.0f} ms · to {checks.get('to') or 'anyone'}",
                log=f"search={checks.get('search')!r} screen={checks.get('screen')} to={checks.get('to')!r}")
    if checks.get("search"):
        trace.stage("search", "running", checks["search"])
    else:
        trace.stage("search", "skipped", "no query")
    return checks


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
        self.speakers = [client.model.name]  # labels the reply is cut at, see labels()
        self.reply = ""
        self.trace_id = trace.current_id()
        self._chunk_count = 0
        self._stream = None
        self._chunks = iter(())
        self._finished = False
        self._lock = threading.Lock()
        # Voice only: keep stage directions to tags the TTS can perform (see utils/VoiceTags.py), none for Kokoro
        voice = getattr(client, "voice", None)
        self.voice_tags = voice.tts_module.tags() if voice is not None else True
        self._tags = VoiceTagFilter(client.model.name, max_tags=1 if self.voice_tags else 0) if source == "voice" else None
        self._at = AtFilter() if source == "voice" else None  # "@lenk" would be read out as "at lenk"
        self._last = None  # last text chunk, used as the template for the filter's final flush

    def labels(self, history) -> list[str]:
        """Stop sequences for this turn's reply: everyone in the history and this turn, plus the bot.
        Without them the model sometimes carries on and writes the next few lines of the chat too."""
        names = [self.client.speaker_name(u) for u in self.said_by]
        self.speakers = speaker_labels(history, names + [self.client.model.name])
        if self._tags:
            self._tags.set_speakers(self.speakers)  # so a leading "Lenk: ..." isn't read out either
        return stop_sequences(self.speakers)

    def prompt(self, text, memories=None, people=None, screen=None, checks=None, seen=None):
        """Open the LLM stream (with the conversation so far as context). `people`: see DiscordClient.people_in.
        screen, seen: a screenshot to show him with it and the line on what's in it (DiscordClient.look_at_screen).
        checks: the reply's pre_checks() (OpenAIModel.pre_checks), None to have them done here."""
        # ? In voice, a hard cap so a "how do I..." question can't turn into a minute-long lecture
        limit = {"max_tokens": VOICE_MAX_TOKENS, "voice": True, "tags": self.voice_tags} if self.source == "voice" else {}
        history = self.client.history(self.channel_id)
        trace.stage("prompt", "done", f"{len(history)} lines of history" + (" · screenshot" if screen else ""),
                    turn=self.trace_id)
        trace.stage("llm", "running", str(getattr(self.client.model.model, "model", "")), turn=self.trace_id)
        try:
            self._stream = self.client.model.generate_stream_text(
                text, related_memories=memories, history=history, stop=self.labels(history), people=people,
                screen=screen, seen=seen, checks=checks, summary=brief.current(self.channel_id), **limit,
                aside=self.client.aside(self.line, history, checks) if self.said_by else None,
            )
        except Exception as e:
            trace.stage("llm", "failed", str(e)[:60], log=repr(e), turn=self.trace_id)
            trace.end("failed", repr(e), turn=self.trace_id)
            raise
        self._chunks = iter(self._stream)

    def __iter__(self):
        return self

    def __next__(self):
        if self._finished:
            raise StopIteration
        try:
            chunk = next(self._chunks)
        except StopIteration:
            trace.stage("llm", "done", f"{self._chunk_count} chunks", turn=self.trace_id)
            # Whatever the filter was still holding back (an unclosed *..)
            rest = self._tags.flush() if self._tags else ""
            rest = self._at.feed(rest) if self._at else rest
            if not rest or self._last is None:
                raise
            chunk, self._last = self._last.model_copy(deep=True), None
            chunk.choices[0].delta.content = rest
            self.reply += rest
            trace.reply(self.reply, turn=self.trace_id)
            return chunk
        except Exception as e:
            trace.stage("llm", "failed", str(e)[:60], log=repr(e), turn=self.trace_id)
            raise
        self._chunk_count += 1
        if chunk.choices and chunk.choices[0].delta.content:
            if self._tags:
                # ? Rewrite the chunk in place so the TTS and the saved reply both get the cleaned text
                chunk.choices[0].delta.content = self._at.feed(self._tags.feed(chunk.choices[0].delta.content))
                self._last = chunk
            self.reply += chunk.choices[0].delta.content
            trace.reply(self.reply, turn=self.trace_id)
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
        if self._stop():
            trace.stage("commit", "skipped", "thrown away", turn=self.trace_id)

    def commit(self):
        """Save this exchange to the conversation, the NAS, and long-term memory (once)."""
        if not self._stop():
            return
        trace.stage("commit", "running", "saving", turn=self.trace_id)
        client, now = self.client, time.time()
        convo = client.conversation(self.channel_id)

        for user, text in self.said_by.items():
            user_id = getattr(user, "id", None)
            content = f"{client.speaker_name(user)}: {text}"
            convo.append({"role": "user", "content": content, "time": now})
            memory.log_message_async(
                client.character, "user", content, user_id=user_id, display_name=client.speaker_name(user),
                username=getattr(user, "name", None), avatar_url=client.avatar_url(user),
                source=self.source, guild_id=self.guild_id, channel_id=self.channel_id,
            )
            # Memory notes come from a brief of the last few minutes, not one LLM call per message
            brief.note(self.channel_id, client.speaker_name(user), text, user_id, client.model, client.character)

        # ? Voice streams to the TTS as it comes, so the stop sequences are what keep a transcript out of
        # the call; this keeps one out of the saved conversation either way
        reply = clean_reply(self.reply, client.model.name, self.speakers)
        if reply:
            convo.append({"role": "assistant", "content": reply, "time": now})
            memory.log_message_async(
                client.character, "assistant", reply,
                source=self.source, guild_id=self.guild_id, channel_id=self.channel_id,
            )
        trace.stage("commit", "done", "saved to the NAS" if reply else "nothing said to save",
                    log=f"reply: {reply}" if reply else None, turn=self.trace_id)