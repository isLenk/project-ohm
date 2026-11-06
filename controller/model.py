# Responsible for the model of the application
from controller.wrappers.openai import OpenAIModel


DEFAULT_ENDPOINT = "http://localhost:5000/v1"
# Handles sending requests to the Text Generation Web UI
class ModelObject:
    def __init__(self, model, api_key="None", endpoint=DEFAULT_ENDPOINT):
        self.name = model["name"]

        self.api_key = api_key
        self.endpoint = endpoint
        self.set_model(model)

    def set_model(self, model):
        #? TODO Use switch if needed
        if model["api_type"] != "openai":
            raise ValueError("Model is not an OpenAI model")
        
        self.model = OpenAIModel(model, self.api_key, self.endpoint)

    def generate_text(self, prompt, related_memories=None, user="user", history=None) -> tuple[str, bool]:
        return self.model.generate_text(prompt, related_memories, user, history=history)

    def extract_memory(self, speaker, text):
        return self.model.extract_memory(speaker, text)
    
    def generate_stream_text(self, prompt, user="user", related_memories=None, history=None):
        return self.model.generate_stream_text(prompt, user, related_memories, history=history)
    
    def get_intent(self, prompt, responses) -> str:
        return self.model.get_intent(prompt, responses)