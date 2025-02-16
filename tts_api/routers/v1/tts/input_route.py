from fastapi import APIRouter, Body, BackgroundTasks
from fastapi.responses import StreamingResponse
from dependencies import play_tts_semaphore, tts_lock, chunk_queue, text_queue
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
    to process."""
    
    try:
        while True:
            data = await websocket.receive_text()
            if data == "END": break
            print(f"{data}", end="")
            text_queue.put(data)
    except fastapi.websockets.WebSocketDisconnect:
        print("Client disconnected.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        text_queue.put(200)


async def send_to_ws(websocket: fastapi.WebSocket):
    try:
        chunks = 0
        while True:
            chunk_ins = None
            try:
                chunk_ins = chunk_queue.get_nowait()
                print(f"Chunk: {chunk_ins}")
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

    except fastapi.websockets.WebSocketDisconnect:
        print("Client disconnected.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        print("Send to ws task ended.")
        if websocket.client_state != fastapi.websockets.WebSocketState.DISCONNECTED:
            await websocket.send_text("END")

def clear_chunk_queue():
    while not chunk_queue.empty():
        chunk_queue.get()


@router.websocket("/ws")
async def websocket_endpoint(websocket: fastapi.WebSocket):

    await websocket.accept()
    
    print("Client connected.")
    send_to_ws_task = asyncio.create_task(send_to_ws(websocket))
    task = asyncio.create_task(read_websocket(websocket))
    engine.feed_from_text_queue()

    await asyncio.gather(task, send_to_ws_task)

    clear_chunk_queue()

    print("Websocket task ended.")    
    return {"status": "success"}
