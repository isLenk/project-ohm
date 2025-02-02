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

    def generate_text(self, prompt) -> tuple[str, bool]:
        return self.model.generate_text(prompt)
    
    def get_intent(self, prompt, responses) -> str:
        return self.model.get_intent(prompt, responses)