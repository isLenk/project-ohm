# Responsible for the model of the application
import os

from dotenv import load_dotenv

from controller.wrappers.openai import OpenAIModel

load_dotenv()


DEFAULT_ENDPOINT = os.getenv("LLM_URL", "http://127.0.0.1:5000/v1")  # ? not localhost: on Windows that tries IPv6 ::1 first and waits ~2s for it to fail
# Handles sending requests to the Text Generation Web UI
class ModelObject:
    def __init__(self, model, api_key="None", endpoint=DEFAULT_ENDPOINT):
        self.name = model["name"]
        self.slug = model.get("slug") or self.name.lower()  # the character's id on the NAS
        self.voice = model.get("voice")  # clip or Kokoro preset, None for the default
        self.voice_engine = model.get("voice_engine") or "chatterbox"

        self.api_key = api_key
        self.endpoint = endpoint
        self.set_model(model)

    def set_model(self, model):
        #? TODO Use switch if needed
        if model["api_type"] != "openai":
            raise ValueError("Model is not an OpenAI model")
        
        self.model = OpenAIModel(model, self.api_key, self.endpoint)

    def generate_text(self, prompt, related_memories=None, user="user", history=None, **kwargs) -> tuple[str, bool]:
        return self.model.generate_text(prompt, related_memories, user, history=history, **kwargs)

    def extract_memory(self, speaker, text):
        return self.model.extract_memory(speaker, text)
    
    def generate_stream_text(self, prompt, user="user", related_memories=None, history=None, **kwargs):
        return self.model.generate_stream_text(prompt, user, related_memories, history=history, **kwargs)
    
    def about_screen(self, prompt) -> bool:
        return self.model.about_screen(prompt)

    def pre_checks(self, prompt) -> dict:
        return self.model.pre_checks(prompt)

    def get_intent(self, prompt, responses) -> str:
        return self.model.get_intent(prompt, responses)