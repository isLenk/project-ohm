# February 1 2025
# Description: Discord module for the bot


import discord
from controller.model import ModelObject
from modules.discord.DiscordVoice import DiscordVoice

testing_channel = 1335351030308802630
voice_channel = 1335374964445941812

class DiscordClient(discord.Client):
    possible_intents = ["join", "leave"]
    model: ModelObject
    logging_channel: int

    @staticmethod
    def initialize(model: ModelObject, testing_channel=testing_channel):
        # intents = discord.Intents.default()
        intents = discord.Intents.all()
        intents.message_content = True
        
        client = DiscordClient(intents=intents)
        client.testing_channel = testing_channel

        client.model = model
        client.voice = DiscordVoice(client, client)

        client.logging_channel = testing_channel

        return client

    async def on_ready(self):
        print(f'Logged on as {self.user}!')

    def log(self, message):
        channel = self.get_channel(self.logging_channel)
        channel.send(message)

    def get_channel(self, id):
        return super().get_channel(id)

    def make_response(self, message):

        response, contains_intent = self.model.generate_text(message.content)

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
            FFMPEG_OPTS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}

            # Stream the message content
            self.voice.vc.play(discord.FFmpegPCMAudio(message.content))
            self.voice.vc.is_playing()
            return
        
        response, contains_intent = self.make_response(message)

        await message.channel.send(response)

        if contains_intent:
            intent = self.model.get_intent(response, self.possible_intents)

            channel = "None" if source == "voice" else message.channel
            return
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
                    await self.voice.join_channel(message)

                case "leave":
                    if source == "voice": return
                    await self.voice.leave_channel(message)
                    print("Leaving voice channel")
                case _:
                    print(f'Intent: {intent}')
        
