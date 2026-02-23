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
    "gen_z": "\033[94m",
    "millennial": "\033[95m",
    "boomer": "\033[93m",
    "regional": "\033[96m",
}

clients: set[WebSocket] = set()
loop: asyncio.AbstractEventLoop | None = None


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


def pipeline_thread():
    """Run mic → Deepgram → detection in a blocking thread."""
    transcriber.run(on_transcript, audio.stream)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop
    loop = asyncio.get_running_loop()

    t = threading.Thread(target=pipeline_thread, daemon=True)
    t.start()

    yield


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
