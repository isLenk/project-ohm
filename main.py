from modules.discord.DiscordClient import DiscordClient 
from controller.model import ModelObject
from config import constants
import os
from dotenv import load_dotenv
from typing import Union
from pyhandler import get_related_memories, get_interface_api, send_input_to_interface, send_modules_to_interface, send_input_to_interface

VERBOSE = True

def println(*args, **kwargs):
    """
    Print to console if VERBOSE is True.
    """
    if VERBOSE:
        print(*args, **kwargs)

load_dotenv()
"""
This script is responsible for initializing the python side of the application.
"""

def run_discord_client(model: ModelObject, token: Union[str, None] = None):
    discord_client = DiscordClient.initialize(model)
    discord_client.run(token)


def process_user_input(model: ModelObject, user_input: str):
    """
    Process user input and return a response.
    """
    interface_api = get_interface_api()
    
    send_input_to_interface(user_input)

    println(f"Sending request to {interface_api} with input: {user_input}")
    
    memories = get_related_memories(user_input)
    response = model.generate_text(user_input, related_memories=memories, user="test_user")
    
    print(f"Response: {response[0]}")

def main():
    token = os.getenv('DISCORD_TOKEN')

    target_model = constants.models["ohm"]
    model = ModelObject(target_model, api_key="0608da5d28eb10cea2914f3de0f3ddba")
    # View all of the modules in the modules directory
    modules = send_modules_to_interface()
    
    # Initialize the models
    # Test model
    text_in = "";

    while text_in != "exit":
        text_in = input("Enter text to generate response (type 'exit' to quit): ")
        if text_in == "exit":
            break

        process_user_input(model, text_in)



if __name__ == '__main__':
    main()