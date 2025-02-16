# February 1 2025
# Description: Discord module for the bot


import discord
from controller.model import ModelObject
from modules.discord.DiscordVoice import DiscordVoice
import asyncio
import threading
import numpy as np
import struct
import wave
import pyaudio
import io
import torchaudio
import websockets
testing_channel = 1335351030308802630
voice_channel = 1335374964445941812

import openai
import utils.AudioFix as AudioFix
class DiscordClient(discord.Client):
    possible_intents = ["join", "leave"]
    model: ModelObject
    logging_channel: int
    voice: DiscordVoice
    
    def add_task(self, func, *args, **kwargs):
        """Add a task to the event loop"""
        self.loop.create_task(func(*args, **kwargs))

    @staticmethod
    def initialize(model: ModelObject, testing_channel=testing_channel):
        intents = discord.Intents.all()
        intents.message_content = True
        
        client = DiscordClient(intents=intents)
        client.testing_channel = testing_channel

        client.model = model
        client.voice = DiscordVoice(client, client)

        client.logging_channel = testing_channel

        return client
     
    async def feed_to_websocket(self, websocket, response):
        for chunk in response:
            if chunk.choices[0].delta.content is None:
                break
            print(chunk.choices[0].delta.content, end="")
            # Feed the chunk to the websocket
            await websocket.send(chunk.choices[0].delta.content)
            
        await websocket.send("END")

    async def on_ready(self):
        print(f'Logged on as {self.user}!')

        vc = await self.join_testing_channel(vc=True)
        
        # client = openai.OpenAI(base_url="http://127.0.0.1:5000/v1", api_key="0608da5d28eb10cea2914f3de0f3ddba")
        # response = client.chat.completions.create(
        #     model="cognitivecomputations_dolphin-2.9-llama3-8b",
        #     messages=[
        #         {
        #             "role": "system",
        #             "content": "You are a helpful assistant."
        #         },
        #         {
        #             "role": "user",
        #             "content": "tell me a short story (2 sentences)"
        #         }
        #     ],
        #     max_tokens=350,
        #     stream=True
        # )

        # websocket = await websockets.connect("ws://localhost:8000/api/v1/tts/ws")
        # feeder = asyncio.create_task(self.feed_to_websocket(websocket, response))
        # await self.voice.receive_from_websocket(websocket)

        # await asyncio.gather(feeder)
        # await websocket.close()
    
    def make_stream_response(self, message):
        """Generate a response for a stream"""
        return self.model.generate_stream_text(message.content, user=message.author.name)
    
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

    def make_response(self, message):
        response, contains_intent = self.model.generate_text(message.content, user=message.author.name)

        # If response contains it's name at the start, remove it
        if response.startswith(self.model.name + ":"):
            response = response[len(self.model.name)+1:]
        
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
        
        response, contains_intent = self.make_response(message)

        await message.reply(response)
        return
        if contains_intent:
            self.parse_intent(message, response, source)
        
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