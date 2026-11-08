import asyncio
import json
import os
import socket
import threading
import time
from urllib.parse import urlsplit

import requests

from controller import modules
from controller.memory import memory
from modules.tts.tts_module import TTS_URL, TTSModule
from modules.vision import screen
from modules.mood.mood import mood

interface_api = "http://localhost:3001"
INTERFACE_TIMEOUT = 3
HEARTBEAT_SECONDS = 5

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


def send_input_to_interface(user_input: str, character: str = "ohm"):
    """
    Send user input to the interface.
    """
    try:
        response = requests.post(f"{interface_api}/api/send", json={
            "character": character,
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


# ? Module health and restarts

def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname, parts.port or 80), timeout=1):
            return True
    except OSError:
        return False


def module_health(client) -> dict:
    """{module: {ok, detail}} for every module. The controller turns changes in `ok` into uptime."""
    voice = client.voice
    vc = getattr(voice, "vc", None)
    in_call = vc is not None and vc.is_connected()
    checks = {
        "discord": lambda: (client.is_ready(), f"Connected as {client.user}" if client.is_ready() else "Not connected to Discord"),
        "memory": lambda: (memory.health(), f"Memory service at {memory.url}") if memory.url else (False, "MEMORY_API_URL isn't set"),
        "tts": lambda: (_reachable(TTS_URL), f"TTS server at {TTS_URL}") if _reachable(TTS_URL) else (False, f"Can't reach the TTS server at {TTS_URL}"),
        # Out of a call there's nothing to hear, which isn't a failure
        "stt": lambda: (vc.is_listening(), f"Listening in {vc.channel}") if in_call else (True, "Ready, not in a voice call"),
        "vision": screen.health,
        "mood": mood.health,
    }
    health = {}
    for name, check in checks.items():
        if not modules.enabled(name):
            health[name] = {"ok": False, "off": True, "detail": "Turned off"}
            continue
        try:
            ok, detail = check()
        except Exception as e:
            ok, detail = False, str(e)
        health[name] = {"ok": bool(ok), "detail": detail}
    return health


def restart_module(client, name: str):
    """What Restart means for each module. They all live in this process, so this resets their state."""
    print(f"[controller] Restarting {name}")
    if name == "memory":
        memory.reset()
    elif name == "tts":
        client.voice.tts_module = TTSModule(client.model)
        # The server came back without the character's voice loaded
        if client.is_ready():
            asyncio.run_coroutine_threadsafe(client.voice.tts_module.preload_voice(), client.loop)
    elif name in ("stt", "discord") and client.is_ready():
        # Rejoining the call gets a new speech-to-text sink and new voice workers
        asyncio.run_coroutine_threadsafe(client.voice.rejoin(), client.loop)


def start_heartbeat(client):
    """Report module health to the controller every few seconds and run the restarts it hands back."""
    def beat():
        while True:
            try:
                response = requests.post(f"{interface_api}/api/modules/heartbeat",
                                         json={"pid": os.getpid(), "modules": module_health(client)},
                                         timeout=INTERFACE_TIMEOUT)
                for name in response.json().get("restart", []):
                    restart_module(client, name)
            except (requests.RequestException, ValueError):
                pass  # the controller app is optional
            time.sleep(HEARTBEAT_SECONDS)

    threading.Thread(target=beat, daemon=True, name="controller-heartbeat").start()
