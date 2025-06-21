import requests
import os
import json
from sentence_transformers import SentenceTransformer

interface_api = "http://localhost:3001"
encoder = SentenceTransformer("all-MiniLM-L6-v2")

def get_interface_api():
    """
    Get the interface API URL.
    """
    return interface_api

def get_encoding(text: str):
    """
    Get the encoding for a given text.
    """
    return encoder.encode(text)

def get_vectorsize():
    """
    Get the size of the vectors used by the encoder.
    """
    return encoder.get_sentence_embedding_dimension()

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
                
                # Send to interface
                response = requests.post(f"{interface_api}/api/modules", json=body)
                if response.status_code != 200:
                    print(f"Error sending module {module} to interface: {response.status_code}")
                else:
                    print(f"Module {module} sent to interface successfully.")
    return modules


def send_input_to_interface(user_input: str):
    """
    Send user input to the interface.
    """
    response = requests.post(f"{interface_api}/api/send", json={
        "character": "ohm",
        "input": str(user_input),
    })
    
    if response.status_code != 200:
        print(f"Error sending input to interface: {response.status_code}")
    else:
        print("Input sent to interface successfully.")

def get_related_memories(text_in: str):
    """
    Get related memories for a given input text.
    """
    encoding = get_encoding(text_in)
    vectorsize = get_vectorsize()
    
    response = requests.post(f"{interface_api}/api/ohm/memories", params={"input": text_in}, json={"encoding": encoding.tolist(), "vectorsize": vectorsize})
    
    if response.status_code == 200:
        memories = response.json()
        print(f"Related Memories: {memories}")
        return memories
    else:
        print(f"Error fetching related memories: {response.status_code}")
        print(f"Response: {response.text}")
        return []