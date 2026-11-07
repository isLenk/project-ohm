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
            **{**self._get_configs(), "temperature": 0.1}  # Make deterministic
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

    def create(self, prompt, system=None, assistant="aight lol", related_memories=None, history=None, **kwargs):
        if system is None:
            system = self.system.strip()
        if related_memories:
            notes = "\n".join(f"- {m['content']}" for m in related_memories)
            system += "\n\nThings you remember (only bring them up if they fit the conversation):\n" + notes

        messages = [{"role": "system", "content": system}]
        if history:
            messages += [{"role": m["role"], "content": m["content"]} for m in history]
        elif assistant:
            messages.append({"role": "assistant", "content": assistant})
        messages.append({"role": "user", "content": prompt})

        # Configs can set temperature/max_tokens; per-call kwargs win over both.
        params = {"temperature": 1, **self._get_configs(), **kwargs}
        return self.client.chat.completions.create(
            model=self.model,
            messages=_merge_same_role(messages),
            **params,
        )

    def generate_stream_text(self, prompt, user="user", related_memories=None, history=None, **kwargs):
        return self.create(prompt, related_memories=related_memories, history=history, stream=True, **kwargs)

    def generate_text(self, prompt, related_memories=None, user="user", history=None):
        response = self.create(prompt, related_memories=related_memories, history=history)
        text = response.choices[0].message.content or ""
        return (text, "[INTENT]" in text)

    def extract_memory(self, speaker, text):
        """Return a short note worth remembering about `speaker` from what they said, or None."""
        system = (
            "You decide what's worth remembering long-term about a person from one thing they said in a chat.\n"
            "Worth remembering: their name, likes and dislikes, plans, job or school, pets, relationships, "
            "life events, opinions they clearly hold.\n"
            "Not worth remembering: greetings, filler, questions, reactions, insults, jokes, small talk, "
            "or anything about the conversation or the bot itself.\n"
            "Requests to the bot are not facts about the person either: \"explain X\", \"how do I Y\", "
            "\"I want you to...\", \"tell me about...\" are all NONE, even if they mention a topic.\n"
            f"If there is something worth remembering, reply with ONE short third-person note that starts "
            f"with their name, for example: \"{speaker} likes apples.\"\n"
            "Otherwise reply with exactly: NONE"
        )
        response = self.create(
            f"{speaker}: {text}", system=system, assistant="",
            temperature=0.1, max_tokens=40,
        )
        note = (response.choices[0].message.content or "").strip().strip('"').strip()
        if not note or note.upper().startswith("NONE") or len(note) > 200:
            return None
        return note


def _merge_same_role(messages):
    """Some chat templates require roles to alternate, so join back-to-back messages from the same role."""
    merged = []
    for m in messages:
        if merged and merged[-1]["role"] == m["role"] and m["role"] != "system":
            merged[-1] = {"role": m["role"], "content": merged[-1]["content"] + "\n" + m["content"]}
        else:
            merged.append(dict(m))
    return merged
