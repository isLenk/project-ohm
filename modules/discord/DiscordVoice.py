from discord.ext import voice_recv
from modules.stt.stt_module import STTModule
from modules.tts.tts_module import TTSModule
import numpy as np
import discord
import subprocess
import asyncio
import io
import aiohttp
import utils.AudioFix as AudioFix
import json
import os
import websockets
# DISCORD_SAMPLE_RATE = 48000
DISCORD_SAMPLE_RATE = 44100
import logging
logger = logging.getLogger(__name__)
import wave
import modules.discord.utils.DiscordVoiceUtil as DC_Util
class DiscordVoice:

    text_queue: asyncio.Queue
    # Audio chunks that are ready to be played (16000 Hz, 16-bit signed PCM, 1 channel)
    audio_out_queue: asyncio.Queue
    listener_worker: asyncio.Task
    output_worker: asyncio.Task
    channel: discord.VoiceChannel
    vc: discord.VoiceClient
    is_playing: bool = False
    processing_response: bool = False
    tts_module: TTSModule

    def __init__(self, client, discord_client):
        self.client = client
        self.discord_client = discord_client
        self.tts = TTSModule()
        self.text_queue = asyncio.Queue()
        self.audio_out_queue = asyncio.Queue()
        self.tts_module = TTSModule()
    
    async def listen_worker(self):
        """Worker that listens to the audio queue and generates text.
        The worker will pause if no text is received within a certain threshold.
        """
        pause_max = 0.01

        while True:
            # A dictionary of participants and their text
            participants = {}

            user, text = await self.text_queue.get()
            pause = 0
            participants[user] = [text]

            # Retrieve more text within the pause_max threshold.
            # The pause will reset if more text is received.
            
            while pause < pause_max:
                try:
                    more_user, more_text = self.text_queue.get_nowait()
                    if more_text:
                        pause = 0
                        # Append the text to the user's text
                        if more_user in participants:
                            participants[more_user].append(more_text)
                        else:
                            participants[more_user] = [more_text]
                except asyncio.QueueEmpty:
                    # print(".", end="")
                    await asyncio.sleep(0.01)
                    pause += 0.1    

            if self.is_playing or self.processing_response:
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
        print("Output worker started")
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
            print("Playing audio file")
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
        """Leave the voice channel"""
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
        if text.strip() == "":
            return
        
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
        
        self.vc.listen(voice_recv.extras.SpeechRecognitionSink(default_recognizer="whisper", text_cb=self.got_text))

    async def on_text(self, text):
        print(f"on_text:\nPrompt='{text}'")
        print(type(text))
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

        text_stream = self.discord_client.make_stream_response(message)

        websocket = await websockets.connect("ws://localhost:8000/api/v1/tts/ws")

        async def fn_push(buf): 
            print(".", end="")
            return await DC_Util.push_buffer_to_queue(buf, self.audio_out_queue)

        await self.tts_module.ws_thread_manager(websocket, text_stream, fn_push)

    async def handle_request_stream(self, text):
        """Handle the request stream from the TTS module"""
        queue = self.audio_out_queue
        sample_rate = 24000
        min_buffer_size = sample_rate * 5
        out_buffer = io.BytesIO()
        self.is_playing = True
        
        async for chunk in self.tts.get_request_stream(text):
            out_buffer.write(chunk)

            if out_buffer.tell() >= min_buffer_size:
                out_buffer = await DC_Util.push_buffer_to_queue(
                    out_buffer, 
                    queue) 
        
        if out_buffer.tell() > 0:
            await DC_Util.push_buffer_to_queue(out_buffer, queue)
        
        while self.is_playing:
            await asyncio.sleep(0.3)        
        
    async def say(self, text):
        await self.handle_request_stream(text)
        while self.vc.is_playing() or self.is_playing:
            await asyncio.sleep(0.5)