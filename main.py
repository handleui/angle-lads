import asyncio
import json
import threading
import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import audio
import config
import detector
import reasoner
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
context_lock = threading.Lock()
context_lines = deque(maxlen=config.GEMINI_CONTEXT_LINES)
llm_reasoner = reasoner.ContextReasoner()
metrics_lock = threading.Lock()
service_started_at = time.time()
latency_samples = {
    "llm_roundtrip_ms": deque(maxlen=100),
    "final_to_explanation_ms": deque(maxlen=100),
}
pipeline_counts = {
    "interim_events": 0,
    "final_events": 0,
    "explanations_emitted": 0,
}


async def broadcast(message: dict):
    data = json.dumps(message)
    dead = set()
    for ws in clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)


def _record_latency(name: str, value_ms: int) -> int:
    bucket = latency_samples[name]
    bucket.append(value_ms)
    return int(sum(bucket) / len(bucket))


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int((percentile / 100.0) * (len(ordered) - 1))
    return ordered[index]


def _latency_summary() -> dict:
    summaries = {}
    for name, bucket in latency_samples.items():
        values = list(bucket)
        summaries[name] = {
            "count": len(values),
            "p50": _percentile(values, 50),
            "p95": _percentile(values, 95),
            "p99": _percentile(values, 99),
            "avg": int(sum(values) / len(values)) if values else None,
        }
    return summaries


async def analyze_and_broadcast(
    text: str, history_snapshot: list[str], final_received_at: float
):
    llm_started_at = time.perf_counter()
    explanation = await asyncio.to_thread(llm_reasoner.explain, text, history_snapshot)
    llm_roundtrip_ms = int((time.perf_counter() - llm_started_at) * 1000)
    final_to_explanation_ms = int((time.perf_counter() - final_received_at) * 1000)

    if explanation is None:
        return

    avg_llm_ms = _record_latency("llm_roundtrip_ms", llm_roundtrip_ms)
    avg_e2e_ms = _record_latency("final_to_explanation_ms", final_to_explanation_ms)
    with metrics_lock:
        pipeline_counts["explanations_emitted"] += 1
    print(
        "[ai] "
        f"{explanation['term']} ({explanation['target_generation']}, "
        f"{explanation['confidence']:.2f}) "
        f"llm={llm_roundtrip_ms}ms e2e={final_to_explanation_ms}ms "
        f"(avg llm={avg_llm_ms}ms avg e2e={avg_e2e_ms}ms)"
    )
    await broadcast(
        {
            "type": "explanation",
            "text": text,
            "timing_ms": {
                "llm_roundtrip": llm_roundtrip_ms,
                "final_to_explanation": final_to_explanation_ms,
                "avg_llm_roundtrip": avg_llm_ms,
                "avg_final_to_explanation": avg_e2e_ms,
            },
            **explanation,
        }
    )


def on_transcript(text: str, is_final: bool):
    if loop is None:
        return

    received_at = time.perf_counter()
    with metrics_lock:
        key = "final_events" if is_final else "interim_events"
        pipeline_counts[key] += 1

    flags = []
    history_snapshot: list[str] = []

    if is_final:
        if llm_reasoner.enabled:
            with context_lock:
                context_lines.append(text)
                history_snapshot = list(context_lines)
        else:
            flags = detector.scan(text)

    for flag in flags:
        color = GEN_COLORS.get(flag["generation"], "")
        print(
            f"{color}[{flag['generation']}]{RESET} "
            f"{flag['term']} → {flag['definition']}"
        )

    msg = {"type": "final" if is_final else "interim", "text": text, "flags": flags}
    asyncio.run_coroutine_threadsafe(broadcast(msg), loop)

    if is_final and llm_reasoner.enabled and history_snapshot:
        asyncio.run_coroutine_threadsafe(
            analyze_and_broadcast(text, history_snapshot, received_at), loop
        )


def pipeline_thread():
    """Run mic → Deepgram → detection in a blocking thread."""
    print("Opening microphone…")
    mic = audio.stream()
    print("Mic ready, connecting to Deepgram…")
    transcriber.run(on_transcript, mic)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop
    loop = asyncio.get_running_loop()
    if llm_reasoner.enabled:
        print(f"AI detector enabled ({llm_reasoner.model})")
    else:
        print(
            "AI detector disabled "
            "(missing GEMINI_API_KEY, using dictionary fallback)"
        )

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


@app.get("/metrics")
async def metrics():
    with metrics_lock:
        counts = dict(pipeline_counts)
    return {
        "uptime_seconds": int(time.time() - service_started_at),
        "connected_clients": len(clients),
        "counts": counts,
        "latency_ms": _latency_summary(),
    }


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
