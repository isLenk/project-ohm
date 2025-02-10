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
# DISCORD_SAMPLE_RATE = 48000
DISCORD_SAMPLE_RATE = 44100
import logging
logger = logging.getLogger(__name__)
import wave
class DiscordVoice:
    audio_queue: asyncio.Queue
    # Audio chunks that are ready to be played (16000 Hz, 16-bit signed PCM, 1 channel)
    audio_out_queue: asyncio.Queue
    listener_worker: asyncio.Task
    output_worker: asyncio.Task
    channel: discord.VoiceChannel
    vc: discord.VoiceClient
    is_playing: bool

    def __init__(self, client, discord_client):
        self.client = client
        self.discord_client = discord_client
        self.tts = TTSModule()
        self.audio_queue = asyncio.Queue()
        self.audio_out_queue = asyncio.Queue()
    
    async def listen_worker(self):
        pause_max = 0.8

        while True:
            text = await self.audio_queue.get()
            prompt_input = [text]
            pause = 0

            # Retrieve more text within the pause_max threshold.
            # The pause will reset if more text is received.
            while pause < pause_max:
                await asyncio.sleep(0.1)
                pause += 0.1    
                try:
                    more_text = self.audio_queue.get_nowait()
                    if more_text:
                        pause = 0
                        prompt_input.append(more_text)
                except asyncio.QueueEmpty:
                    print("_", end="")

            if "".join(prompt_input).strip() == "":
                print("No text received")
                continue
            prompt = "\n".join(prompt_input)

            # Replace ohm: with blank
            prompt = prompt.replace("Ohm:", "")
    
            if not self.vc.is_playing():
                self.on_text(prompt)

    async def output_worker(self):
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
            print("Playing audio file")
            
            # sampled_audio = audio_file
            # Convert to 16-bit signed PCM
            self.is_playing = True
            formatted = discord.FFmpegPCMAudio(audio_file, **ffmpeg_options, pipe=True)
            self.vc.play(formatted)
            while self.vc.is_playing():
                await asyncio.sleep(1)
            self.is_playing = False

    
    def shutdown(self):
        self.stt.shutdown()

    async def join_channel(self, channel):
        # self.stt = STTModule(use_microphone=False)

        self.vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        await self.listen()
        return self.vc

    async def leave_channel(self, message):
        # try:
            # self.stt.recorder.shutdown()
        # except:
            # pass
        if message.guild.voice_client:
            await message.guild.voice_client.disconnect()

    def got_text(self, user, text):
        # If text is empty, return
        if text.strip() == "":
            return
        
        print(text, end=" | ")
        self.audio_queue.put_nowait(f"{user}: {text}")

    
    async def listen(self):
        # Create a listener worker
        self.listener_worker = self.discord_client.add_task(self.listen_worker)
        self.output_worker = self.discord_client.add_task(self.output_worker)

        self.vc.listen(voice_recv.extras.SpeechRecognitionSink(default_recognizer="whisper", text_cb=self.got_text))

    def on_text(self, text):
        print("Generating | Prompt:", text)
        return ""
        # response, contains_intent = self.discord_client.model.generate_text(text)

        # self.discord_client.add_task(self.say, response)

    async def handle_request_stream(self, text):
        print("Handling request stream")

        # 24000 to 8000
        sample_rate = 24000
        min_buffer_size = sample_rate * 5
        out_buffer = io.BytesIO()
        self.is_playing = True
        
        async for chunk in self.tts.get_request_stream(text):
            out_buffer.write(chunk)

            if out_buffer.tell() >= min_buffer_size:
                out_buffer.seek(0)
                print("Filling buffer")
                await self.audio_out_queue.put(io.BytesIO(out_buffer.getvalue()))
                out_buffer = io.BytesIO()                

        print("Finished loading")
        if out_buffer.tell() > 0:
            await self.audio_out_queue.put(io.BytesIO(out_buffer.getvalue()))

        while self.is_playing:
            await asyncio.sleep(0.1)
        print("Finished playing")
        
        
