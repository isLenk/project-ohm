from discord.ext import voice_recv
from modules.stt.stt_module import STTModule
from modules.tts.tts_module import TTSModule
import numpy as np
import discord
import subprocess
import asyncio
import io
import aiohttp

class DiscordVoice:
    def __init__(self, client, discordClient):
        self.client = client
        self.discordClient = discordClient
        self.tts = TTSModule()
    
    def shutdown(self):
        self.stt.shutdown()

    async def join_channel(self, message):
        self.stt = STTModule(use_microphone=False)

        self.vc = await message.author.voice.channel.connect(cls=voice_recv.VoiceRecvClient)
        await self.listen()

    async def leave_channel(self, message):
        try:
            self.stt.recorder.shutdown()
        except:
            pass
        if message.guild.voice_client:

            await message.guild.voice_client.disconnect()

    
    async def listen(self):
        self.vc.listen(voice_recv.BasicSink(self.on_listen))

    def on_listen(self, user, data: voice_recv.VoiceData):
        # print("Message from", user)
        # print(data)
        
        # Convert the raw PCM audio data to a numpy array
        pcm_data = np.frombuffer(data.pcm, dtype=np.int16)

        # Feed the audio data to RealtimeSTT
        self.stt.recorder.feed_audio(pcm_data.tobytes())

        # print("Audio data:", pcm_data)
        # Print parsed audio data
        self.stt.recorder.text(self.on_text)

    def on_text(self, text):
        message = {}
        message["content"] = text
        response, contains_intent = self.discordClient.model.generate_text(text)

        self.discordClient.add_task(self.say, response)

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
