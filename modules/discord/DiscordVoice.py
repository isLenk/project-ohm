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
    

    def shutdown(self):
        self.stt.shutdown()

    async def join_channel(self, channel):
        self.stt = STTModule(use_microphone=False)

        self.vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        await self.listen()
        return self.vc

    async def leave_channel(self, message):
        try:
            self.stt.recorder.shutdown()
        except:
            pass
        if message.guild.voice_client:

            await message.guild.voice_client.disconnect()

    def got_text(self, user, text):
        # If text is empty, return
        if text.strip() == "":
            return
        
        print(f"Recognized text from {user}: {text}")

    async def listen(self):
        self.vc.listen(voice_recv.extras.SpeechRecognitionSink(default_recognizer="whisper", text_cb=self.got_text))

    def on_text(self, text):
        message = {}
        message["content"] = text
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
