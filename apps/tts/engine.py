
from fastapi import Body
from fastapi import BackgroundTasks
from fastapi.responses import StreamingResponse
from RealtimeTTS import TextToAudioStream
from streaming_chatterbox import StreamingChatterboxEngine
import threading
from typing import Union
from queue import Queue
from dependencies import cprint, create_wave_header_for_engine, log, chunk_queue, text_queue

class TTSEngine:
    stream: str
    engine_stream: TextToAudioStream
    audio_queue: Queue
    chunks_received: int
    pending_feed: Queue

    # ? voice=None uses Chatterbox Turbo's built-in (female) voice. Pass a .wav path (5s+) to clone one instead, e.g. "voices/lance.wav"
    def load_engine(self, model_name="chatterbox", voice=None, overwrite=False, *args, **kwargs):
        if hasattr(self, "engine") and overwrite == False and self.engine.engine_name == model_name: 
            cprint("red", "Model already loaded.")
            return "Model already loaded."
        self.unload_engine()
        self.audio_queue = Queue()
        self.chunks_received = 0
        # ? Streams audio mid-sentence (~0.3s to first audio instead of waiting for the whole sentence)
        self.engine = StreamingChatterboxEngine(voice=voice, *args, **kwargs)
        self.warm_up()
        self.engine_stream = TextToAudioStream(self.engine, on_audio_stream_stop=self.on_audio_stream_stop)
        self.pending_feed = Queue() 

        formatting, channel, sample_rate = self.engine.get_stream_info()
        cprint("blue", f"Model: {model_name}, Voice: {voice}")
        cprint("blue", f"Format: {formatting}, Channels: {channel}, Sample Rate: {sample_rate}")
        # Read the 
        cprint("blue", "Model Loaded")

    def warm_up(self):
        """Synthesize a throwaway sentence so CUDA kernels and caches are set up before the first real reply.
        Long enough to hit both the mid-sentence and final vocode passes."""
        import time
        start = time.perf_counter()
        try:
            self.engine.synthesize("Hey, just warming up my voice here, give me one second and I'll be ready to talk.")
        except Exception as e:
            cprint("red", f"Warm-up failed: {e!r}")
        # Throw away the warm-up audio so it doesn't play on the first connection
        while not self.engine.queue.empty():
            self.engine.queue.get_nowait()
        cprint("blue", f"Warmed up in {time.perf_counter() - start:.1f}s")

    def unload_engine(self):
        if not hasattr(self, "engine"):
            cprint("red", "No engine to unload.")
            return
        try:
            self.engine_stream.stop()
            self.engine.shutdown()
        except Exception:
            pass
    
    def _ensure_engine(func):
        """Decorator to ensure engine is loaded before running a function"""
        def wrapper(self, *args, **kwargs):
            if not hasattr(self, "engine"):
                cprint("red", "No engine loaded.")
                return "No engine loaded."
            return func(self, *args, **kwargs)
        return wrapper
    
    def _on_audio_chunk(self, chunk):
        """Callback for handling audio chunks"""
        self.chunks_received += 1
        try:
            chunk_queue.put(chunk)
        except Exception as e:
            cprint("red", e)
            
    @staticmethod
    def _openai_generator(gen_stream):
        """Generator for OpenAI streaming API.
        Yields sentences as they are completed."""
        payload = ""
        log("Starting generator...")
        for chunk in gen_stream:
            if (content := chunk.choices[0].delta.content) is not None:
                print(content, end="-")
                ends = ["?", ".", "!"]
                results = [end in content for end in ends]
                if any(results):
                    payload += content
                    # Get index of last found end in content
                    last = max([payload.rindex(ends[i]) for i, x in enumerate(results) if x])
                    feed, payload = payload[:last+1], payload[last+1:]
                        
                    yield feed
                else:
                    payload += content

        if payload.strip() != "":
            yield payload

        log("Generator finished.")
 
    @_ensure_engine
    def on_audio_stream_stop(self):
        """Callback for when the audio stream stops"""
        print("Audio stream stopped.")
        # Check if there are pending feeds
        chunk_queue.put(200)
        self.pending_feed = Queue()
        
    @_ensure_engine
    def finish_input(self):
        """Finish the input stream"""
        self.audio_queue.put(None)
        self.pending_feed = Queue()

    def handle_ws(self):
        while True:
            var = text_queue.get(block=True)
            if var == 200: break
            yield var

    @_ensure_engine
    def reset_stream(self):
        """Stop any synthesis still running and drop leftover text/audio.
        Without this, a response the client hung up on (interrupt) keeps synthesizing into the
        shared queues and its audio and end marker leak into the next connection."""
        stream = self.engine_stream
        if stream.is_playing():
            # ? Not using stream.stop(): it closes the audio stream from this thread while the play
            # thread closes it too, which crashes the whole server. Ask the play thread to wind down instead.
            for abort_event in list(stream.abort_events):
                abort_event.set()
            self.engine.stop()          # Drop the sentence currently being synthesized
            stream.char_iter.stop()
            text_queue.put(200)         # handle_ws may be blocked waiting for text
            if stream.play_thread is not None:
                stream.play_thread.join(timeout=15)
            stream._create_iterators()  # Fresh text iterator for the next feed (same as stop() does)
        for q in (text_queue, chunk_queue):
            while not q.empty():
                q.get_nowait()

    @_ensure_engine
    def feed_from_text_queue(self):
        """Feed from the text queue to the engine"""
        self.engine_stream.feed(self.handle_ws()).play_async(muted=True, on_audio_chunk=self._on_audio_chunk)

engine = TTSEngine()