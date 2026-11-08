# Text Generation Web UI Handler
# Implements the OpenAI API for LlamaChat 
import time
from datetime import date

import openai

from config.constants import VOICE_PROMPT
from controller.adapters import Adapters
from controller.memory import format_people, memory
from modules.mood.mood import mood
from utils import WebSearch

GLOSSARY_HISTORY = 6  # recent messages also checked for terms, so a reply to earlier slang still gets it


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
        self.slug = model.get("slug") or self.name.lower()  # the character's id, same as ModelObject.slug
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
        # The character's LoRA adapter, sent with every request (see controller/adapters.py)
        self.adapters = Adapters(model, endpoint, fetch_character=memory.get_character)

    def _extra_body(self, base=False):
        """{"lora": [...]} for one request; base=True runs it on the plain base model."""
        lora = self.adapters.lora(base=base)
        return {"lora": lora} if lora is not None else None

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
            **{**self._get_configs(), "temperature": 0.1},  # Make deterministic
            extra_body=self._extra_body(base=True),
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

    def create(self, prompt, system=None, assistant="aight lol", related_memories=None, history=None, voice=False,
               web_results=None, glossary=True, base=False, people=None, images=None, screen=None, **kwargs):
        """One chat request. Replies run with the character's adapter; base=True (utility calls) without.
        images: data URLs (utils/Vision.py) of pictures sent with `prompt`.
        screen: a screenshot data URL (modules/vision/screen.py) of the screen being shared with him."""
        if system is None:
            system = self.system.strip()
            if voice:
                system += "\n\n" + VOICE_PROMPT.format(name=self.name).strip()
            # How he feels right now (modules/mood), one line at most; the tag hint only means something out loud
            feeling = " ".join(s for s in (mood.line(), mood.tag_hint() if voice else "") if s)
            if feeling:
                system += "\n\n" + feeling
        if glossary:
            # Slang, game names and typos from the Curator's General terminology that come up here.
            recent = " ".join(m["content"] for m in (history or [])[-GLOSSARY_HISTORY:])
            terms = memory.glossary_for(recent + "\n" + prompt)
            if terms:
                system += "\n\nWords people here use, so you know what they mean:\n" + "\n".join(terms)
        if people:
            # Who the message is about: every name they go by and what's remembered about each (memory.people_in)
            system += ("\n\nPeople who came up, and what you know about them (only bring it up if it fits; "
                       "the names in brackets are the same person):\n" + format_people(people))
            known = {n for p in people for n in p.get("notes") or []}
            related_memories = [m for m in related_memories or [] if m["content"] not in known]
        if related_memories:
            notes = "\n".join(f"- {m['content']}" for m in related_memories)
            system += "\n\nThings you remember (only bring them up if they fit the conversation):\n" + notes
        if web_results:
            system += ("\n\nYou just looked this up online. Use it to answer, in your own words. "
                       "Don't read out links or say \"according to\":\n" + web_results)
        if images:
            system += ("\n\nThey just sent you a picture, and you can see it. React to what's actually in it "
                       "the way a friend would: a quick comment, a joke, or an answer if they asked something. "
                       "Don't describe it back to them piece by piece.")
        if screen:
            system += ("\n\nYou can see the screen someone here is sharing with you right now (the last picture). "
                       "Use it when it's relevant: answer what they ask about it, or react to what's on it like "
                       "a friend looking over their shoulder. Keep it short and don't describe the whole screen.")

        messages = [{"role": "system", "content": system}]
        if history:
            messages += [{"role": m["role"], "content": m["content"]} for m in history]
        elif assistant:
            messages.append({"role": "assistant", "content": assistant})
        messages.append({"role": "user", "content": prompt})
        messages = _merge_same_role(messages)
        pictures = [*(images or []), *([screen] if screen else [])]
        if pictures:
            # After merging, which only joins plain text: the pictures go with the newest user message
            messages[-1]["content"] = [{"type": "text", "text": messages[-1]["content"]}] + [
                {"type": "image_url", "image_url": {"url": url}} for url in pictures]

        # Configs can set temperature/max_tokens; per-call kwargs win over both.
        params = {"temperature": 1, **self._get_configs(), **kwargs}
        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            extra_body=self._extra_body(base),
            **params,
        )

    def search_query(self, prompt):
        """Return a web search query if answering `prompt` needs one, else None. Quick and cheap (~0.1s)."""
        system = (
            f"Today is {date.today():%B %d, %Y}.\n"
            "You decide whether a chat message needs a web search to answer well.\n"
            "Search for: news, sports results, weather, prices, release dates, game updates, recent events, "
            "or specific facts (people, places, numbers) that could have changed or that you might get wrong.\n"
            "Don't search for: greetings, small talk, jokes, opinions, feelings, advice, "
            "or anything about the people in the chat.\n"
            "If it needs a search, reply with only a short search query. Otherwise reply with exactly: NONE"
        )
        response = self.create(prompt, system=system, assistant="", base=True, temperature=0, max_tokens=24)
        query = (response.choices[0].message.content or "").strip()
        query = query.removeprefix("Search for:").strip().strip('"').strip()
        if not query or query.upper().startswith("NONE"):
            return None
        return query

    def about_screen(self, prompt):
        """Whether `prompt` wants him to look at the shared screen. Quick and cheap, like search_query."""
        system = (
            "You decide whether a chat message wants the bot to look at someone's computer screen.\n"
            "YES: asking it to look or watch (\"look at this\", \"see this?\", \"check this out\"), asking about "
            "something on screen (\"what do you think of this\", \"which one should i pick\", \"what does this "
            "say\", \"how do i beat this\"), or about what they're doing, playing or watching right now.\n"
            "NO: anything else, including small talk and questions that don't need to see anything.\n"
            "Reply with exactly YES or NO."
        )
        response = self.create(prompt, system=system, assistant="", base=True, glossary=False,
                               temperature=0, max_tokens=2)
        return (response.choices[0].message.content or "").strip().upper().startswith("YES")

    def look_up(self, prompt):
        """Search the web if `prompt` needs it. Returns the results as text, or None.
        Each search also shows up in the dashboard's Web search tab."""
        query = self.search_query(prompt)
        if not query:
            return None
        started = time.perf_counter()
        results, error = WebSearch.search(query)
        WebSearch.report(self.slug, prompt, query, results, error, round((time.perf_counter() - started) * 1000))
        return WebSearch.as_text(results)

    def generate_stream_text(self, prompt, user="user", related_memories=None, history=None, **kwargs):
        return self.create(prompt, related_memories=related_memories, history=history, stream=True,
                           web_results=self.look_up(prompt), **kwargs)

    def generate_text(self, prompt, related_memories=None, user="user", history=None, **kwargs):
        response = self.create(prompt, related_memories=related_memories, history=history,
                               web_results=self.look_up(prompt), **kwargs)
        text = response.choices[0].message.content or ""
        return (text, "[INTENT]" in text)

    def extract_memory(self, speaker, text):
        """Return a short note worth remembering about `speaker`, or someone they mention, from what they said, or None."""
        system = (
            "You decide what's worth remembering long-term about the people in a chat from one thing someone said. "
            "It can be about the speaker, or about someone they talk about.\n"
            "Worth remembering: names (a real name, a nickname), likes and dislikes, plans, job or school, pets, "
            "relationships, life events, opinions they clearly hold.\n"
            "Not worth remembering: greetings, filler, questions, reactions, insults, jokes, small talk, "
            "or anything about the conversation or the bot itself.\n"
            "Requests to the bot are not facts about the person either: \"explain X\", \"how do I Y\", "
            "\"I want you to...\", \"tell me about...\" are all NONE, even if they mention a topic.\n"
            f"If there is something worth remembering, reply with ONE short third-person note that starts "
            f"with that person's name as the message gives it, for example: \"{speaker} likes apples.\" or "
            f"\"Kyle just started a new job.\" (\"I\", \"me\" and \"my\" are {speaker}.)\n"
            "Otherwise reply with exactly: NONE"
        )
        response = self.create(
            f"{speaker}: {text}", system=system, assistant="", base=True,
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
