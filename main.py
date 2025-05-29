from modules.discord.DiscordClient import DiscordClient 
from controller.model import ModelObject
from config import constants

import os
from dotenv import load_dotenv
import asyncio

from typing import Union
from fastapi import FastAPI
from fastapi import Body
import requests
load_dotenv()
import json
# app = FastAPI()

"""
This script is responsible for initializing the python side of the application.
"""
interface_api = "http://localhost:3001"

def send_modules_to_interface():
    """
    Look through modules directory and send them to the interface.
    """
    # Get all of the modules in the modules directory
    modules = []
    for module in os.listdir("modules"):
        # Ensure is a directory
        if os.path.isdir(os.path.join("modules", module)):
            # Ensure __init__.py exists
            if os.path.exists(os.path.join("modules", module, "__init__.py")):
                modules.append(module)
                # Read body.json
                with open(os.path.join("modules", module, "body.json"), "r") as f:
                    body = f.read()
                body = json.loads(body)
                
                # Send to interface
                response = requests.post(f"{interface_api}/api/modules", json=body)
                if response.status_code != 200:
                    print(f"Error sending module {module} to interface: {response.status_code}")
                else:
                    print(f"Module {module} sent to interface successfully.")
    return modules

def run_discord_client(model: ModelObject, token: Union[str, None] = None):
    discord_client = DiscordClient.initialize(model)
    discord_client.run(token)

def main():
    token = os.getenv('DISCORD_TOKEN')
    target_model = constants.models["ohm"]

    model = ModelObject(target_model, api_key="0608da5d28eb10cea2914f3de0f3ddba")
    # View all of the modules in the modules directory
    modules = send_modules_to_interface()
    
    # Initialize the model
    # Test model
    text_in = "";
    addy = "http://localhost:8000"

    while text_in != "exit":
        text_in = input("Enter text to generate response (type 'exit' to quit): ")
        if text_in == "exit":
            break

        requests.post(f"{addy}/send", json={
            "character": "ohm",
            "input": str(text_in),
            })
        
        response = model.generate_text(text_in, user="test_user")
        print(f"Response: {response[0]}")

if __name__ == '__main__':
    main()