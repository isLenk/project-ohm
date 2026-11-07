import io
import wave
"""Utility functions for Discord voice"""


async def push_buffer_to_queue(buffer, queue):
    """Push a buffer to a queue"""
    buffer.seek(0)
    # await queue.put(io.BytesIO(buffer.getvalue()))
    temp_buffer = io.BytesIO()
    with wave.open(temp_buffer, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(24000)
        f.writeframes(buffer.getvalue())
    temp_buffer.seek(0)
    await queue.put(temp_buffer)
    
    buffer = io.BytesIO()

    return buffer

# ? Temporarily Stripped froom tts_api/runner.py
def openai_generator(gen_stream):
    """Generator for OpenAI streaming API.
    Yields sentences as they are completed."""
    payload = ""
    for chunk in gen_stream:
        if (content := chunk.choices[0].delta.content) is not None:
            # print(content, end="-")
            ends = ["?", ".", "!"]
            results = [end in content for end in ends]
            if any(results):
                payload += content
                # Get index of last found end in content
                last = max([payload.rindex(ends[i]) for i, x in enumerate(results) if x])
                feed, payload = payload[:last+1], payload[last+1:]
                # print("EOL")
                yield feed
            else:
                payload += content

    if payload.strip() != "":
        yield payload


class Message:
    def __init__(self, content, author):
        self.content = content
        self.author = author

class Author:
    def __init__(self, name):
        self.name = name