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
# DISCORD_SAMPLE_RATE = 48000
DISCORD_SAMPLE_RATE = 44100

class DiscordVoice:
    audio_queue: asyncio.Queue
    listener_worker: asyncio.Task

    def __init__(self, client, discord_client):
        self.client = client
        self.discord_client = discord_client
        self.tts = TTSModule()
        self.audio_queue = asyncio.Queue()
    
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

        self.vc.listen(voice_recv.extras.SpeechRecognitionSink(default_recognizer="whisper", text_cb=self.got_text))

    def on_text(self, text):
        print("Generating | Prompt:", text)
        response, contains_intent = self.discord_client.model.generate_text(text)

        self.discord_client.add_task(self.say, response)

    async def say(self, text, out=None):
        if out is None:
            out = self.vc
        stream_url = await self.tts.get_stream(text)

        ffmpeg_options = {
            'options': '-vn'
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(stream_url) as stream:
                chunk = await stream.content.read()
                out.play(discord.FFmpegPCMAudio(io.BytesIO(chunk), **ffmpeg_options, pipe=True))
                while out.is_playing() or out.is_paused():
                    await asyncio.sleep(0.1)
