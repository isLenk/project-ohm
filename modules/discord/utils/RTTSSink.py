from discord.ext import voice_recv
from discord import Member
from typing import Literal, Callable, Optional, Any, Final, Protocol, Awaitable, TypeVar
import asyncio

from RealtimeSTT import AudioToTextRecorder

class CustomSpeechRecognitionSink(voice_recv.AudioSink):
    def __init__(self, *, text_cb=None, recognizer=None):
        super().__init__()
        self.text_cb = text_cb
        self.recognizer_name = recognizer

        self.recognizer = AudioToTextRecorder(use_microphone=False, spinner=False)

    def wants_opus(self):
        return False
    
    def _await(self, coro):
        assert self.client is not None
        return asyncio.run_coroutine_threadsafe(coro, self.client.loop)
    
    chunks_read = 50
    # : User | Member | None
    def write(self, user, data):
        
        if user is None:
            return

        self.recognizer.feed_audio(data.pcm)
        self.recognizer.text(lambda text: self.text_cb(user, text))
        
    def background_listener(self, user):
        process_cb = self.process_cb
        text_cb = self.text_cb

    # TODO: Implement.
    def cleanup(self) -> None:
        print("CLEANUP")