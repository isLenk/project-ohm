from modules.discord.DiscordClient import DiscordClient
from controller.model import ModelObject
from config import constants

import os
from dotenv import load_dotenv
import asyncio

load_dotenv()

def main(discord_client: DiscordClient):
    discord_client.run(token)

if __name__ == '__main__':
    token = os.getenv('DISCORD_TOKEN')
    
    # ! TEMP
    target_model = constants.models["ohm"]

    loop = asyncio.get_event_loop_policy().get_event_loop()
    # Load the model
    model = ModelObject(target_model)
    discord_client = DiscordClient.initialize(model)

    try:
        # loop.run_until_complete(main(discord_client=discord_client))
        main(discord_client=discord_client)
    except KeyboardInterrupt:
        print("Shutting down...")
        # loop.run_until_complete(discord_client.voice.shutdown())
        # loop.run_until_complete(discord_client.close())
        # loop.run_until_complete(asyncio.sleep(1))
        # loop.close()