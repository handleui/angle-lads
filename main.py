import asyncio
import json
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import audio
import detector
import transcriber

RESET = "\033[0m"
GEN_COLORS = {
    "gen_z": "\033[94m",      # blue
    "millennial": "\033[95m",  # magenta
    "boomer": "\033[93m",      # yellow
    "regional": "\033[96m",    # cyan
}

clients: set[WebSocket] = set()
loop: asyncio.AbstractEventLoop | None = None
dg_connection = None


async def broadcast(message: dict):
    data = json.dumps(message)
    dead = set()
    for ws in clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)


def on_transcript(text: str, is_final: bool):
    if loop is None:
        return

    flags = detector.scan(text) if is_final else []

    for flag in flags:
        color = GEN_COLORS.get(flag["generation"], "")
        print(
            f"{color}[{flag['generation']}]{RESET} "
            f"{flag['term']} → {flag['definition']}"
        )

    msg = {"type": "final" if is_final else "interim", "text": text, "flags": flags}
    asyncio.run_coroutine_threadsafe(broadcast(msg), loop)


def audio_thread():
    if dg_connection is None:
        return
    audio.stream(dg_connection.send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop, dg_connection
    loop = asyncio.get_running_loop()

    dg_connection = transcriber.connect(on_transcript)

    mic = threading.Thread(target=audio_thread, daemon=True)
    mic.start()

    yield

    if dg_connection:
        dg_connection.finish()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
