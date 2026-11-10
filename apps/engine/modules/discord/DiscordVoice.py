from discord.ext import voice_recv
from modules.tts.tts_module import TTSModule, WS_URL
import numpy as np
import discord
import asyncio
import utils.AudioFix as AudioFix
import websockets
import time
import random
from modules.discord.utils.RTTSSink import CustomSpeechRecognitionSink
from modules.discord.utils.StreamingPCMSource import StreamingPCMSource
import utils.latency as latency
import logging
from modules.mood.mood import mood
from modules.vision import watch

DISCORD_SAMPLE_RATE = 44100
IDLE_SECONDS = 10   # how long the call has to be quiet before Ohm might talk on his own
IDLE_CHANCE = 0.5   # chance he does each time that happens, so it feels occasional, not like a timer
logger = logging.getLogger(__name__)

# What to do when someone talks over him (a plain word check, no LLM call, so it's instant)
FILLER = {"yeah", "yea", "yep", "yup", "ya", "yes", "ok", "okay", "k", "right", "true", "sure", "mhm", "mm",
          "hmm", "hm", "uh", "huh", "um", "ah", "oh", "lol", "lmao", "haha", "ha", "nice", "cool", "facts", "fair", "word"}
STOP_PHRASES = ("shut up", "stfu", "stop", "be quiet", "quiet", "enough", "shush", "shh", "nobody asked")
STOP_MAX_WORDS = 5   # "dude just shut up ohm" counts, a longer sentence that happens to say "stop" doesn't
FILLER_MAX_WORDS = 3

def classify_interruption(text: str, bot_name: str = "") -> str:
    """'ignore' (backchannel, keep talking), 'stop' (go quiet, don't reply) or 'answer' (stop and reply)."""
    words = "".join(c if c.isalnum() or c == "'" else " " for c in text.lower()).split()
    words = [w for w in words if w != bot_name.lower()]   # "okay ohm" is still just "okay"
    if not words:
        return "ignore"
    phrase = " ".join(words)
    if len(words) <= STOP_MAX_WORDS and any(f" {p} " in f" {phrase} " for p in STOP_PHRASES):
        return "stop"
    if len(words) <= FILLER_MAX_WORDS and all(w in FILLER for w in words):
        return "ignore"
    return "answer"
# ? Discord sends RTCP sender reports that voice_recv logs on every packet, they're harmless
logging.getLogger("discord.ext.voice_recv.reader").setLevel(logging.WARNING)
import wave
import modules.discord.utils.DiscordVoiceUtil as DC_Util
class DiscordVoice:

    listener_worker: asyncio.Task
    output_worker: asyncio.Task
    text_queue: asyncio.Queue
    # Audio chunks that are ready to be played (16000 Hz, 16-bit signed PCM, 1 channel)
    audio_out_queue: asyncio.Queue
    channel: discord.VoiceChannel
    vc: discord.VoiceClient
    tts_module: TTSModule
    is_playing: bool = False
    processing_response: bool = False
    current_source: StreamingPCMSource | None = None
    response_task: asyncio.Task | None = None # The reply currently being generated/played
    current_prompt: dict = {}                 # What that reply is answering
    current_turn = None                       # The Turn (see DiscordClient) behind that reply
    last_heard: float = 0.0                   # time.monotonic() of the last sign of conversation
    spoke_unprompted: bool = False            # Already broke the current silence; wait for someone to talk
    barge_watch: asyncio.Task | None = None   # Resumes him if someone cut in but nothing came of it

    def __init__(self, client, discord_client):
        self.client = client
        self.discord_client = discord_client
        self.text_queue = asyncio.Queue()
        self.audio_out_queue = asyncio.Queue()

        self.tts_module = TTSModule(client.model)
        mood.name = client.model.name
    

    async def _poll_for_more_text(self, pause_max, participants):
        """Poll for more text within a certain threshold. Used in listen_worker
        """
        pause = 0
        while pause < pause_max:
            try:
                more_user, more_text = self.text_queue.get_nowait()
                if not more_text: continue

                pause = 0
                # Append the text to the user's text
                if more_user in participants:
                    participants[more_user].append(more_text)
                else:
                    participants[more_user] = [more_text]

            except asyncio.QueueEmpty:
                # ? not using asyncio.io to prevent switching to another task
                time.sleep(0.01)
                pause += 0.1

        return participants

    async def listen_worker(self, pause_max=0.01):
        """Worker that listens to the audio queue and generates text.
        The worker will pause if no text is received within a certain threshold.
        """

        while True:
            # A dictionary of participants and their text
            participants = {}

            user, text = await self.text_queue.get()
            participants[user] = [text]

            # Retrieve more text within the pause_max threshold.
            # The pause will reset if more text is received.
            participants = await self._poll_for_more_text(pause_max, participants)

            # Who said what, e.g. {Lenk: "anyone up for a game?", Sam: "sure"}
            said_by = {user: " ".join(texts).strip() for user, texts in participants.items()}
            said_by = {user: text for user, text in said_by.items() if text}
            if not said_by:
                print("listen_worker: No text received")
                continue

            if self.is_busy():
                was_talking = self.talking()
                self.cancel_barge_watch()
                if was_talking:
                    action = classify_interruption(" ".join(said_by.values()), self.discord_client.model.name)
                    if action == "ignore":
                        print("[voice] Backchannel, still talking")
                        if self.vc.is_paused(): self.vc.resume()
                        continue
                    if action == "stop":
                        mood.shushed()
                        await self.interrupt()
                        print("[voice] Told to stop")
                        continue
                previous = self.current_prompt
                if not was_talking and self.current_turn is not None:
                    # Nobody heard that reply, so don't keep it in the conversation
                    self.current_turn.discard()
                await self.interrupt()
                if was_talking:
                    # Talking over her shuts her up, then she answers what they said
                    mood.interrupted()
                    print("[voice] Interrupted, answering that instead")
                else:
                    # She hadn't started talking yet, so they were still mid-thought: answer it all together.
                    # ? One line each, newest last, like training data, so the newest gets answered
                    print("[voice] More speech before she answered, restarting with all of it")
                    said_by = {u: "\n".join(t for t in (previous.get(u, ""), said_by.get(u, "")) if t).strip()
                               for u in {**previous, **said_by}}

            # ? Runs in the background so this loop can keep listening and interrupt it
            self.current_prompt = said_by
            self.response_task = asyncio.create_task(self.on_text(said_by))

    def talking(self) -> bool:
        """True while his audio is playing, including while paused because someone cut in.
        ? discord.py's is_playing() is False while paused"""
        return bool(self.vc and (self.vc.is_playing() or self.vc.is_paused()))

    def is_busy(self):
        """True while a reply is being generated or played"""
        return (self.response_task is not None and not self.response_task.done()) or self.talking()

    def on_speech(self, user):
        """Someone has been talking for a bit (BARGE_IN_MS) and hasn't finished yet. If he's talking,
        pause him right away instead of waiting for their transcript; listen_worker decides what to do once it arrives."""
        if not (self.vc and self.vc.is_playing()):
            return
        self.vc.pause()
        print(f"[voice] {user} is cutting in, paused")
        self.cancel_barge_watch()
        self.barge_watch = asyncio.create_task(self._resume_if_nothing_said())

    async def _resume_if_nothing_said(self):
        """If they stop talking and no transcript turns up (noise, empty transcription), carry on."""
        while self.sink.anyone_speaking():
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.6) # Time for the transcript to arrive (slowest path is SILENCE_MS, already waited out above)
        if self.vc and self.vc.is_paused():
            print("[voice] Nothing to answer, carrying on")
            self.vc.resume()

    def cancel_barge_watch(self):
        if self.barge_watch is not None and not self.barge_watch.done():
            self.barge_watch.cancel()
        self.barge_watch = None

    async def interrupt(self):
        """Stop the current reply: cancel generation (LLM + TTS) and cut the audio"""
        task, self.response_task = self.response_task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.current_source is not None:
            self.current_source.clear()
            self.current_source = None
        self.cancel_barge_watch()
        if self.talking():
            # ? Not vc.stop(): on a VoiceRecvClient that stops listening too, so every interruption made him
            # deaf until ensure_listening noticed, and dropped what people were saying in the meantime
            self.vc.stop_playing()

    async def output_worker(self):
        """Worker that plays audio files from the audio_out_queue"""
        while True:
            if self.vc is None:
                print("No voice client")
                await asyncio.sleep(0.1)
                continue

            ffmpeg_options = {
                'options': '-vn'
            }
            audio_file = None
            try:
                audio_file = await self.audio_out_queue.get()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.1)
                print("No audio file")
                continue
                
            while self.vc.is_playing() or self.vc.is_paused():
                await asyncio.sleep(0.1)
                
            self.is_playing = True
            formatted = discord.FFmpegPCMAudio(audio_file, **ffmpeg_options, pipe=True)
            self.vc.play(formatted)
            while self.vc.is_playing():
                await asyncio.sleep(0.5)
            self.is_playing = False
    
    def shutdown(self):
        self.stt.shutdown()

    async def join_channel(self, channel) -> discord.VoiceClient:
        """Join the voice channel. Return the voice client"""

        self.vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        await self.listen()
        return self.vc

    async def leave_channel(self, message) -> None:
        """Clean up the workers andd leave the voice channel
        """
        await self.interrupt()
        if message.guild.voice_client:
            await message.guild.voice_client.disconnect()
        try:
            self.listener_worker.cancel()
            self.output_worker.cancel()
            self.idle_task.cancel()
            self.watch_task.cancel()
        except Exception as e:
            print(e)
        finally:
            self.vc = None

    async def rejoin(self) -> None:
        """Leave the call and join it again: fresh workers and a fresh speech-to-text sink (the controller's Restart)."""
        if self.vc is None:
            return
        channel = self.vc.channel
        await self.interrupt()
        for task in (self.listener_worker, self.output_worker, self.idle_task, self.watch_task):
            task.cancel()
        await self.vc.disconnect()
        self.vc = None
        await self.join_channel(channel)

    def got_text(self, user, text):
        """Callback for when text is received from the listener"""
        # If text is empty, return
        if text.strip() == "": return
        
        if self.processing_response:
            print("PROCESSING - Skipped")
            return
        
        print(text, end=" | ")
        self.last_heard = time.monotonic()
        self.spoke_unprompted = False
        mood.heard(text, mentioned=self.discord_client.model.name.lower() in text.lower())
        self.text_queue.put_nowait((user, text))

    async def listen(self):
        """Listen to the voice channel and start the listener workers"""
        # Create a listener worker
        self.listener_worker = self.discord_client.add_task(self.listen_worker)
        self.output_worker = self.discord_client.add_task(self.output_worker)
        self.last_heard = time.monotonic()
        self.idle_task = self.discord_client.add_task(self.idle_worker)
        self.watch_task = self.discord_client.add_task(watch.watch_worker, self)  # returns at once if vision is off
        self.start_listening()

    def start_listening(self):
        """(Re)start receiving audio. A fresh sink each time: voice_recv cleans the old one up when it stops."""
        # ? SilenceGeneratorSink fills in silence when Discord stops sending packets, so we can tell when someone is done talking
        self.sink = CustomSpeechRecognitionSink(recognizer="rtts", text_cb=self.got_text, speech_cb=self.on_speech)
        self.vc.listen(voice_recv.SilenceGeneratorSink(self.sink), after=self._listening_stopped)

    def _listening_stopped(self, error):
        """voice_recv stops listening for good if anything throws while it handles a packet (runs on its thread)"""
        if error is not None:
            print(f"[voice] Stopped hearing audio: {error!r}")

    def ensure_listening(self):
        """Called every second: if receiving died while still in the call, start it again"""
        if self.vc is None or not self.vc.is_connected() or self.vc.is_listening():
            return
        print("[voice] Wasn't receiving audio anymore, restarting it")
        try:
            self.start_listening()
        except Exception as e:
            print(f"[voice] Couldn't restart listening: {e!r}")

    async def on_text(self, said_by):
        """said_by: {discord user: what they said} for everyone who spoke this turn."""
        print("\n", "-" * 20, f"\nPrompt={ {str(u): t for u, t in said_by.items()} }")
        if not said_by:
            return
        await self._speak(self.discord_client.make_stream_response, said_by, self.vc.channel)

    async def speak_unprompted(self, about_screen=False, region=None):
        """Say something on his own because the call has gone quiet (about_screen: about something new on the screen,
        region: where on it, see watch.changed_region)"""
        print("\n", "-" * 20, "\n[voice] Quiet for a while, saying something")
        await self._speak(self.discord_client.make_idle_stream, self.vc.channel, about_screen, region)

    async def idle_worker(self):
        """Every so often, if nobody has said anything for IDLE_SECONDS, maybe start talking.
        He only does it once per silence: after that he waits for someone to talk first."""
        while True:
            await asyncio.sleep(1)
            if self.vc is None or not self.vc.is_connected():
                continue
            self.ensure_listening()
            if self.is_busy() or self.sink.anyone_speaking():
                self.last_heard = time.monotonic()
                continue
            mood.quiet(time.monotonic() - self.last_heard)
            if self.spoke_unprompted or time.monotonic() - self.last_heard < IDLE_SECONDS:
                continue

            self.last_heard = time.monotonic()  # Not this time? Try again after another stretch of silence
            if not any(not m.bot for m in self.vc.channel.members) or random.random() > mood.idle_chance(IDLE_CHANCE):
                continue
            self.spoke_unprompted = True
            self.current_prompt = {}
            self.response_task = asyncio.create_task(self.speak_unprompted())

    async def _speak(self, make_turn, *args):
        """Generate a reply with make_turn(*args) and play it as it streams in"""
        # ? One continuous source per response: chunks are played as soon as they
        # arrive instead of being batched into separate ffmpeg clips
        def on_start():
            latency.mark("playback")
            latency.report()

        source = StreamingPCMSource(in_rate=24000, on_start=on_start)
        self.current_source = source

        def on_done(error):
            if error: print(f"Playback error: {error}")

        async def fn_push(chunk):
            latency.mark("first_tts_audio")
            source.feed(chunk)
            if not self.talking():
                self.vc.play(source, after=on_done)

        latency.mark("bot_got_text")
        turn = None
        # ? If we're cancelled while make_turn is still running on its thread, the Turn it returns
        # is never seen here, so it has to close its own LLM stream once it exists
        pending = {"cancelled": False, "turn": None}
        def make():
            pending["turn"] = t = make_turn(*args)
            if pending["cancelled"]:
                t.discard()
            return t
        try:
            # ? Memory lookups + the LLM request are blocking HTTP calls, keep them off the event loop.
            # Connect to TTS at the same time instead of after
            async def connect_tts():
                # In the character's voice (ws_url may ask the NAS for it, so off the event loop)
                return await websockets.connect(await asyncio.to_thread(self.tts_module.ws_url))

            turn, websocket = await asyncio.gather(
                asyncio.to_thread(make),
                connect_tts(),
            )
            self.current_turn = turn
            latency.mark("llm_stream_open")
            await self.tts_module.ws_thread_manager(websocket, turn, fn_push, raw=True)
        except Exception as e:
            print(f"Error connecting to TTS WebSocket: {e}")
        finally:
            source.finish()
            # Save the exchange (a no-op if it was discarded for a restart). If he got cut off,
            # this keeps what he'd said so far.
            if turn is not None:
                turn.commit()
                if turn.reply:
                    mood.spoke()
            else:
                pending["cancelled"] = True
                if pending["turn"] is not None:
                    pending["turn"].discard()

        print("-" *  20)