from discord.ext import voice_recv
from modules.tts.tts_module import TTSModule
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

DISCORD_SAMPLE_RATE = 44100
IDLE_SECONDS = 10   # how long the call has to be quiet before Ohm might talk on his own
IDLE_CHANCE = 0.5   # chance he does each time that happens, so it feels occasional, not like a timer
logger = logging.getLogger(__name__)
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

    def __init__(self, client, discord_client):
        self.client = client
        self.discord_client = discord_client
        self.tts = TTSModule()
        self.text_queue = asyncio.Queue()
        self.audio_out_queue = asyncio.Queue()
        
        self.tts_module = TTSModule()
    

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
                was_talking = self.vc.is_playing()
                previous = self.current_prompt
                if not was_talking and self.current_turn is not None:
                    # Nobody heard that reply, so don't keep it in the conversation
                    self.current_turn.discard()
                await self.interrupt()
                if was_talking:
                    # Talking over her just shuts her up
                    print("[voice] Interrupted")
                    continue
                # She hadn't started talking yet, so they were still mid-thought: answer it all together
                print("[voice] More speech before she answered, restarting with all of it")
                said_by = {u: f"{previous.get(u, '')} {said_by.get(u, '')}".strip() for u in {**previous, **said_by}}

            # ? Runs in the background so this loop can keep listening and interrupt it
            self.current_prompt = said_by
            self.response_task = asyncio.create_task(self.on_text(said_by))

    def is_busy(self):
        """True while a reply is being generated or played"""
        return (self.response_task is not None and not self.response_task.done()) or bool(self.vc and self.vc.is_playing())

    async def interrupt(self):
        """Stop the current reply: cancel generation (LLM + TTS) and cut the audio"""
        task, self.response_task = self.response_task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.current_source is not None:
            self.current_source.clear()
            self.current_source = None
        if self.vc and self.vc.is_playing():
            self.vc.stop()

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
        except Exception as e:
            print(e)
        finally:
            self.vc = None

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
        self.text_queue.put_nowait((user, text))

    async def listen(self):
        """Listen to the voice channel and start the listener workers"""
        # Create a listener worker
        self.listener_worker = self.discord_client.add_task(self.listen_worker)
        self.output_worker = self.discord_client.add_task(self.output_worker)
        self.last_heard = time.monotonic()
        self.idle_task = self.discord_client.add_task(self.idle_worker)

        # ? SilenceGeneratorSink fills in silence when Discord stops sending packets, so we can tell when someone is done talking
        self.sink = CustomSpeechRecognitionSink(recognizer="rtts", text_cb=self.got_text)
        self.vc.listen(voice_recv.SilenceGeneratorSink(self.sink))

    async def on_text(self, said_by):
        """said_by: {discord user: what they said} for everyone who spoke this turn."""
        print("\n", "-" * 20, f"\nPrompt={ {str(u): t for u, t in said_by.items()} }")
        if not said_by:
            return
        await self._speak(self.discord_client.make_stream_response, said_by, self.vc.channel)

    async def speak_unprompted(self):
        """Say something on his own because the call has gone quiet"""
        print("\n", "-" * 20, "\n[voice] Quiet for a while, saying something")
        await self._speak(self.discord_client.make_idle_stream, self.vc.channel)

    async def idle_worker(self):
        """Every so often, if nobody has said anything for IDLE_SECONDS, maybe start talking.
        He only does it once per silence: after that he waits for someone to talk first."""
        while True:
            await asyncio.sleep(1)
            if self.vc is None or not self.vc.is_connected():
                continue
            if self.is_busy() or self.sink.anyone_speaking():
                self.last_heard = time.monotonic()
                continue
            if self.spoke_unprompted or time.monotonic() - self.last_heard < IDLE_SECONDS:
                continue

            self.last_heard = time.monotonic()  # Not this time? Try again after another stretch of silence
            if not any(not m.bot for m in self.vc.channel.members) or random.random() > IDLE_CHANCE:
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
            if not self.vc.is_playing():
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
            turn, websocket = await asyncio.gather(
                asyncio.to_thread(make),
                websockets.connect("ws://127.0.0.1:8000/api/v1/tts/ws"),
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
            else:
                pending["cancelled"] = True
                if pending["turn"] is not None:
                    pending["turn"].discard()

        print("-" *  20)