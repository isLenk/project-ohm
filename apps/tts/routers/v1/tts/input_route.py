from fastapi import APIRouter, Body, BackgroundTasks
from fastapi.responses import StreamingResponse
from dependencies import play_tts_semaphore, tts_lock, chunk_queue, text_queue, session_lock
from engine import engine
import threading
import asyncio
import fastapi

router = APIRouter(
    tags=["TTS"],
    responses={404: {"description": "Not found"}},
)
    
@router.post("/stop_stream")
async def post_stop_stream():
    engine.stop_stream()
    return {"status": "success"}

@router.post("/finish_input")
async def post_finish_input():
    engine.finish_input()
    return {"status": "success"}


async def read_websocket(websocket: fastapi.WebSocket):
    """Retrieve data from websocket, feed into text_queue for handle_ws
    to process. Keeps listening after END so a client that hangs up mid-response
    (interrupt) is noticed right away. Returns once the client disconnects."""
    ended = False
    try:
        while True:
            data = await websocket.receive_text()
            if ended: continue
            if data == "END":
                ended = True
                text_queue.put(200)
                continue
            print(f"{data}", end="")
            text_queue.put(data)
    except fastapi.websockets.WebSocketDisconnect:
        print("Client disconnected.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if not ended: text_queue.put(200)


async def send_to_ws(websocket: fastapi.WebSocket):
    try:
        chunks = 0
        while True:
            chunk_ins = None
            try:
                chunk_ins = chunk_queue.get_nowait()
            except Exception as e: pass

            if chunk_ins == 200:
                print("EOS")
                break
            
            if chunk_ins is None:
                await asyncio.sleep(0.01)
                continue
            if ((chunks := chunks + 1) % 100) == 0:
                print(f"100 Chunks sent.")

            await websocket.send_bytes(chunk_ins)

        await websocket.send_text("END")
    except fastapi.websockets.WebSocketDisconnect:
        print("Client disconnected.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        print("Send to ws task ended.")


async def use_voice(name: str, engine_name: str = "chatterbox"):
    """Switch to the character's engine and voice. Falls back to the engine's default, then Chatterbox's"""
    try:
        await asyncio.to_thread(engine.set_voice, name, engine_name)
    except Exception as e:
        print(f"Can't use {engine_name} voice {name!r} ({e}), using its default voice.")
        try:
            await asyncio.to_thread(engine.set_voice, "", engine_name)
        except Exception as e:
            print(f"Can't use {engine_name} at all ({e}), using Chatterbox's built-in voice.")
            await asyncio.to_thread(engine.set_voice, "", "chatterbox")


@router.websocket("/ws")
async def websocket_endpoint(websocket: fastapi.WebSocket):
    """?voice=<file in voices/> speaks the reply as that clip ("" for the built-in voice), &engine=kokoro as that
    Kokoro voice. Without ?voice=, the last engine and voice are kept."""

    await websocket.accept()

    async with session_lock:
        print("Client connected.")
        # ? Clear out anything left from a previous client that hung up mid-response
        await asyncio.to_thread(engine.reset_stream)
        voice = websocket.query_params.get("voice")
        if voice is not None:
            # instant unless the clip is new or Kokoro isn't loaded
            await use_voice(voice, websocket.query_params.get("engine") or "chatterbox")

        send_to_ws_task = asyncio.create_task(send_to_ws(websocket))
        engine.feed_from_text_queue()
        read_task = asyncio.create_task(read_websocket(websocket))

        await asyncio.wait({send_to_ws_task, read_task}, return_when=asyncio.FIRST_COMPLETED)
        if read_task.done() and not send_to_ws_task.done():
            # Client hung up before getting all the audio (interrupt), stop sending right away
            send_to_ws_task.cancel()
        # Otherwise everything was sent; the client closes once it reads END
        await asyncio.gather(send_to_ws_task, return_exceptions=True)
        read_task.cancel()
        await asyncio.gather(read_task, return_exceptions=True)

        # Stop synthesizing whatever text was left (interrupt) and drop leftover audio
        await asyncio.to_thread(engine.reset_stream)

        print("Websocket task ended.")
