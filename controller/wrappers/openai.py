# Text Generation Web UI Handler
# Implements the OpenAI API for LlamaChat 
import openai

class OpenAIModel:
    client: openai.OpenAI
    model: str
    system: str
    max_tokens: int
    temperature: float
    top_p: float

    memory: str

    def __init__(self, model, api_key, endpoint):
        self.name = model["name"]
        self.model = model["model"]
        self.system = model["personality"]
        # If there is a "configs" key in the model, set the values
        # Not all configs are required
        for key in model["configs"]:
            setattr(self, key, model["configs"][key])
        
        # Print all the attributes
        print(f"Loaded OpenAI Model ({self.name}):")
        for key in self.__dict__:
            print(f"{key}: {self.__dict__[key]}")

        if api_key == "None": print("No API Key Provided")
        self.client = openai.OpenAI(
            base_url=endpoint,
            api_key=api_key
        )
        self.memory = ""

    def get_intent(self, prompt, responses: list):
        # Get the intent from the user's response
        intents = ", ".join(responses)
        print("Possible Intents:", intents)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "Classify the user's intent (and only respond with the intent) as one of the following (or None if there is no intent): " + intents},
                {"role": "user", "content": prompt}
            ],
            **self._get_configs(),
            temperature=0.1 # Make deterministic
        )

        # Get the intent from the response
        intent = response.choices[0].message.content
        print("Raw Intent Reading:", intent)
        # If the intent is not in the list of possible intents, return None
        if intent not in responses:
            return None
    
        return intent

    def _get_configs(self):
        configs = {}
        if hasattr(self, "max_tokens"):
            configs["max_tokens"] = self.max_tokens
        if hasattr(self, "temperature"):
            configs["temperature"] = self.temperature
        if hasattr(self, "top_p"):
            configs["top_p"] = self.top_p
        return configs
    
    def create(self, prompt, user, **kwargs):
        return self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system},
                {"role": "assistant", "content": "aight lol"},
                {"role": "user", "content": prompt}
            ],
            temperature=1,
            **self._get_configs(),
            **kwargs
        )

    def generate_stream_text(self, prompt, user="user"):
        response = self.create(prompt, user,  stream=True)

        return response
    
    def generate_text(self, prompt, user="user"):
        response = self.create(prompt, user)

        contains_intent = "[INTENT]" in response.choices[0].message.content
        return (response.choices[0].message.content, contains_intent)