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
    # Audio chunks that are ready to be played (16000 Hz, 16-bit signed PCM, 1 channel)
    audio_out_queue: asyncio.Queue
    listener_worker: asyncio.Task
    output_worker: asyncio.Task
    channel: discord.VoiceChannel
    vc: discord.VoiceClient

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
            try:
                audio_file = await self.audio_out_queue.get()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.1)
                print("No audio file")
                continue
                
            while self.vc.is_playing() or self.vc.is_paused():
                await asyncio.sleep(0.1)
            print("Playing audio file")
            
            formatted = discord.FFmpegPCMAudio(audio_file, **ffmpeg_options, pipe=True)
            # self.vc.play(discord.FFmpegPCMAudio("dry-fart.mp3"))
            self.vc.play(formatted)

    
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
        response, contains_intent = self.discord_client.model.generate_text(text)

        self.discord_client.add_task(self.say, response)

    currently_processing = None
    async def process_audio_stream(self, first_chunk):
        if self.currently_processing:
            print("Already processing audio stream")
            return
        
        self.currently_processing = True
        
        chunks_collected = np.array([])
        chunks_per_push = 500
        chunks_count = 0
        chunks_read = 0
        async for chunk in self.tts.get_request_stream(first_chunk):
            chunk_data, end_of_stream = chunk
            # self.audio_out_queue.put_nowait(io.BytesIO(chunk_data))

            # Append the chunk to the array
            chunks_collected = np.append(chunks_collected, chunk_data)
            chunks_count += 1
            chunks_read += 1

            if (chunks_read % 250) == 0:
                print("CHUNKS-", chunks_read)
            # If we have enough chunks, push them to the audio queue
            if chunks_count >= chunks_per_push or end_of_stream:
                self.audio_out_queue.put_nowait(io.BytesIO(chunks_collected))
                chunks_collected = np.array([])
                chunks_count = 0

            if end_of_stream:
                print("\n------------- EOS -------------\n")
                break
        print("Chunks Read:", chunks_read)
            # await asyncio.sleep(0.1)
        self.currently_processing = None
        print("Done processing audio stream")

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
