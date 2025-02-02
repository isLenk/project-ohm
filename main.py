from modules.discord.DiscordClient import DiscordClient
from controller.model import ModelObject
from config import constants
import signal

import os
from dotenv import load_dotenv

load_dotenv()
def main():
    default_handler = signal.getsignal(signal.SIGINT)
    token = os.getenv('DISCORD_TOKEN')

    # ! TEMP
    target_model = constants.models["ohm"]

    # Load the model
    model = ModelObject(target_model)

    discordClient = DiscordClient.initialize(model)
    discordClient.run(token)

    def on_shutdown(sig, frame):
        print("Shutting down...")
        discordClient.voice.shutdown()
        default_handler(sig, frame)

    signal.signal(signal.SIGINT, on_shutdown)


if __name__ == '__main__':
    main()