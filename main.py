from modules.discord.DiscordClient import DiscordClient
from controller.model import ModelObject
from config import constants

import os
from dotenv import load_dotenv
import asyncio

from typing import Union
from fastapi import FastAPI
from fastapi import Body

load_dotenv()

def main():
    token = os.getenv('DISCORD_TOKEN')
    
    # ! TEMP
    target_model = constants.models["ohm"]

    # Load the model
    model = ModelObject(target_model)
    discord_client = DiscordClient.initialize(model)

    discord_client.run(token)

if __name__ == '__main__':
    main()