import faulthandler
import time as _time

# ? A crash in native code (opus, onnxruntime, libsndfile...) kills the process with only an exit code
# (0xC0000374 is heap corruption). This writes every thread's Python stack to crash.log when that happens.
if not faulthandler.is_enabled():
    _crash_log = open("crash.log", "a", encoding="utf-8")
    _crash_log.write(f"\n--- main.py started {_time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    _crash_log.flush()
    faulthandler.enable(file=_crash_log, all_threads=True)

from modules.discord.DiscordClient import DiscordClient
from controller.model import ModelObject
from config import constants
from controller.memory import active_character, load_character
import os
from dotenv import load_dotenv
from typing import Union
from pyhandler import get_related_memories, get_interface_api, send_input_to_interface, send_modules_to_interface, start_heartbeat

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
    start_heartbeat(discord_client)
    discord_client.run(token)

def process_user_input(model: ModelObject, user_input: str):
    """
    Process user input and return a response.
    """
    interface_api = get_interface_api()
    
    send_input_to_interface(user_input, model.slug)

    println(f"Sending request to {interface_api} with input: {user_input}")
    
    memories = get_related_memories(user_input, model.slug)
    response = model.generate_text(user_input, related_memories=memories, user="test_user")
    
    print(f"Response: {response[0]}")

def build_model() -> ModelObject:
    # The NAS is the source of truth for characters; constants.py is the offline fallback.
    # Runs as the card in play in the controller (see active_character); a new card that leaves
    # out its model or configs gets Ohm's.
    slug = active_character()
    target_model = load_character(slug, fallback=constants.models.get(slug), defaults=constants.models["ohm"])
    return ModelObject(target_model, api_key="0608da5d28eb10cea2914f3de0f3ddba")

def main():

    token = os.getenv('DISCORD_TOKEN')

    model = build_model()
    # View all of the modules in the modules directory
    modules = send_modules_to_interface()
    
    run_discord_client(model, token)
    return
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