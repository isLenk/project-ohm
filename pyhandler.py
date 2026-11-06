import requests
import os
import json
from controller.memory import memory

interface_api = "http://localhost:3001"
INTERFACE_TIMEOUT = 3

def get_interface_api():
    """
    Get the interface API URL.
    """
    return interface_api

# ? Interface Related Functions Below

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
                
                # Send to interface (the controller app is optional)
                try:
                    response = requests.post(f"{interface_api}/api/modules", json=body, timeout=INTERFACE_TIMEOUT)
                except requests.RequestException:
                    print(f"Controller not reachable at {interface_api}; skipping module registration.")
                    return modules
                if response.status_code not in (200, 201):
                    print(f"Error sending module {module} to interface: {response.status_code}")
                else:
                    print(f"Module {module} sent to interface successfully.")
    return modules


def send_input_to_interface(user_input: str):
    """
    Send user input to the interface.
    """
    try:
        response = requests.post(f"{interface_api}/api/send", json={
            "character": "ohm",
            "input": str(user_input),
        }, timeout=INTERFACE_TIMEOUT)
    except requests.RequestException:
        print("Controller not reachable; input not sent to interface.")
        return

    if response.status_code != 200:
        print(f"Error sending input to interface: {response.status_code}")
    else:
        print("Input sent to interface successfully.")

def get_related_memories(text_in: str, character: str = "ohm"):
    """
    Get related memories for a given input text from the memory service on the NAS.
    """
    memories = memory.search(character, text_in)
    print(f"Related Memories: {[m['content'] for m in memories]}")
    return memories
