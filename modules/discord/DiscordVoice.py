from discord.ext import voice_recv
from modules.tts.tts_module import TTSModule
import numpy as np
import discord
import asyncio
import utils.AudioFix as AudioFix
import websockets
import time
from modules.discord.utils.RTTSSink import CustomSpeechRecognitionSink
import logging

DISCORD_SAMPLE_RATE = 44100
logger = logging.getLogger(__name__)
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

            if self.is_playing or self.processing_response: 
                # Interrupt the audio
                await self.tts_module.interrupt()
                
                # Clean out audio queue
                while not self.audio_out_queue.empty():
                    self.audio_out_queue.get_nowait()

                # Reset the flag
                self.processing_response = False
                self.is_playing = False

                continue

            prompt_input = [f"{user}: {text}" for user, text in participants.items()]
            if "".join(prompt_input).strip() == "":
                print("listen_worker: No text received")
                continue
    
            prompt = "\n".join(prompt_input)
    
            if not self.vc.is_playing():
                await self.on_text(prompt)

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
        if message.guild.voice_client:
            await message.guild.voice_client.disconnect()
        try:
            self.listener_worker.cancel()
            self.output_worker.cancel()
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
        self.text_queue.put_nowait((user, text))

    async def listen(self):
        """Listen to the voice channel and start the listener workers"""
        # Create a listener worker
        self.listener_worker = self.discord_client.add_task(self.listen_worker)
        self.output_worker = self.discord_client.add_task(self.output_worker)
        
        self.vc.listen(CustomSpeechRecognitionSink(recognizer="rtts", text_cb=self.got_text))

    async def on_text(self, text):
        print("\n", "-" * 20, f"\nPrompt='{text}'")
        if text.strip() == "" or self.processing_response or self.is_playing:
            return
        
        class Message:
            def __init__(self, content, author):
                self.content = content
                self.author = author

        class Author:
            def __init__(self, name):
                self.name = name
    
        author = Author("user")
        message = Message(str(text), author)

        async def fn_push(buf): 
            print(".", end="")
            return await DC_Util.push_buffer_to_queue(buf, self.audio_out_queue)

        text_stream = self.discord_client.make_stream_response(message)
        
        try:
            websocket = await websockets.connect("ws://localhost:8000/api/v1/tts/ws")
            await self.tts_module.ws_thread_manager(websocket, text_stream, fn_push)
        except Exception as e:
            print(f"Error connecting to TTS WebSocket: {e}")

        print("-" *  20)