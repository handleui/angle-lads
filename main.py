import asyncio
import json
import math
import traceback
import threading
import time
from array import array
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import audio
import config
import detector
import openai_reasoner
import openai_transcriber
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
ai_worker_task: asyncio.Task | None = None
ai_queue: asyncio.Queue | None = None
pending_ai_item = None
pending_ai_lock = threading.Lock()
context_lock = threading.Lock()
context_lines = deque(maxlen=config.GEMINI_CONTEXT_LINES)
llm_reasoner = (
    openai_reasoner.OpenAIContextReasoner()
    if config.EXPLANATION_PROVIDER == "openai"
    else reasoner.ContextReasoner()
)
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
    "ai_jobs_enqueued": 0,
    "ai_jobs_dropped": 0,
}
pipeline_state = {
    "status": "idle",
    "last_error": None,
    "deepgram_status": "idle",
    "deepgram_detail": None,
    "last_event_at": None,
    "last_transcript": None,
    "last_transcript_kind": None,
    "audio_chunks_sent": 0,
    "audio_level": 0,
    "ai_queue_depth": 0,
    "ai_error": None,
    "transcription_provider": config.TRANSCRIPTION_PROVIDER,
    "explanation_provider": config.EXPLANATION_PROVIDER,
}
AI_QUEUE_MAXSIZE = 8


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
    index = min(
        round((percentile / 100.0) * (len(ordered) - 1)),
        len(ordered) - 1,
    )
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
        with metrics_lock:
            pipeline_state["ai_error"] = llm_reasoner.last_error
        return

    avg_llm_ms = _record_latency("llm_roundtrip_ms", llm_roundtrip_ms)
    avg_e2e_ms = _record_latency("final_to_explanation_ms", final_to_explanation_ms)
    with metrics_lock:
        pipeline_counts["explanations_emitted"] += 1
        pipeline_state["ai_error"] = None
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


async def ai_worker():
    global pending_ai_item
    while True:
        try:
            item = await ai_queue.get()
        except asyncio.CancelledError:
            break

        try:
            if item is None:
                break
            text, history_snapshot, final_received_at = item
            await analyze_and_broadcast(text, history_snapshot, final_received_at)
            while True:
                with pending_ai_lock:
                    pending = pending_ai_item
                    if pending is None:
                        break
                    pending_ai_item = None
                await analyze_and_broadcast(*pending)
        finally:
            with metrics_lock:
                pipeline_state["ai_queue_depth"] = ai_queue.qsize()
            ai_queue.task_done()


def on_transcript(text: str, is_final: bool):
    global pending_ai_item
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
    with metrics_lock:
        pipeline_state["last_event_at"] = time.time()
        pipeline_state["last_transcript"] = text[-160:]
        pipeline_state["last_transcript_kind"] = "final" if is_final else "interim"
    asyncio.run_coroutine_threadsafe(broadcast(msg), loop)

    if is_final and llm_reasoner.enabled:
        item = (text, history_snapshot, received_at)
        try:
            ai_queue.put_nowait(item)
            with metrics_lock:
                pipeline_counts["ai_jobs_enqueued"] += 1
                pipeline_state["ai_queue_depth"] = ai_queue.qsize()
        except asyncio.QueueFull:
            with pending_ai_lock:
                pending_ai_item = item
            with metrics_lock:
                pipeline_counts["ai_jobs_dropped"] += 1
                pipeline_state["ai_queue_depth"] = ai_queue.qsize()


def on_pipeline_status(event: str, detail: str | None):
    with metrics_lock:
        pipeline_state["last_event_at"] = time.time()

        if event == "connected":
            pipeline_state["status"] = "running"
            pipeline_state["deepgram_status"] = "connected"
            pipeline_state["deepgram_detail"] = None
            return

        if event == "connecting":
            pipeline_state["status"] = "starting"
            pipeline_state["deepgram_status"] = "connecting"
            pipeline_state["deepgram_detail"] = detail
            return

        if event == "reconnecting":
            pipeline_state["status"] = "degraded"
            pipeline_state["deepgram_status"] = "reconnecting"
            pipeline_state["deepgram_detail"] = detail
            return

        if event == "stopped":
            pipeline_state["status"] = "stopped"
            pipeline_state["deepgram_status"] = "stopped"
            pipeline_state["deepgram_detail"] = detail
            return

        if event == "transcript":
            pipeline_state["deepgram_status"] = "streaming"
            pipeline_state["deepgram_detail"] = detail


def tracked_audio_stream(rate: int, chunk_size: int):
    for chunk in audio.stream(rate=rate, chunk=chunk_size):
        samples = array("h")
        samples.frombytes(chunk)
        peak = max((abs(sample) for sample in samples), default=0)
        rms = math.sqrt(
            sum(sample * sample for sample in samples) / len(samples)
        ) if samples else 0.0
        peak_level = (peak / 32767) ** 0.35 if peak else 0.0
        rms_level = (rms / 32767) ** 0.3 if rms else 0.0
        normalized_level = min(100, int(max(peak_level, rms_level) * 100))
        with metrics_lock:
            pipeline_state["audio_chunks_sent"] += 1
            pipeline_state["last_event_at"] = time.time()
            previous_level = pipeline_state["audio_level"]
            if normalized_level >= previous_level:
                smoothed_level = int((previous_level * 0.4) + (normalized_level * 0.6))
            else:
                smoothed_level = int((previous_level * 0.82) + (normalized_level * 0.18))
            pipeline_state["audio_level"] = max(0, min(100, smoothed_level))
        yield chunk


def pipeline_thread():
    """Run mic → Deepgram → detection in a blocking thread."""
    with metrics_lock:
        pipeline_state["status"] = "starting"
        pipeline_state["last_error"] = None
        pipeline_state["deepgram_status"] = "starting"
        pipeline_state["deepgram_detail"] = None
        pipeline_state["last_event_at"] = time.time()
        pipeline_state["last_transcript"] = None
        pipeline_state["last_transcript_kind"] = None
        pipeline_state["audio_chunks_sent"] = 0
        pipeline_state["audio_level"] = 0
        pipeline_state["ai_queue_depth"] = 0
        pipeline_state["ai_error"] = None

    try:
        print("Opening microphone…")
        if config.TRANSCRIPTION_PROVIDER == "openai":
            sample_rate = config.OPENAI_AUDIO_RATE
            chunk_size = config.OPENAI_AUDIO_CHUNK
            runner = openai_transcriber.run
            print("Mic ready, connecting to OpenAI Realtime…")
        else:
            sample_rate = config.DEEPGRAM_AUDIO_RATE
            chunk_size = config.DEEPGRAM_AUDIO_CHUNK
            runner = transcriber.run
            print("Mic ready, connecting to Deepgram…")

        mic = tracked_audio_stream(sample_rate, chunk_size)
        with metrics_lock:
            pipeline_state["status"] = "running"
            pipeline_state["deepgram_status"] = "connecting"
        runner(on_transcript, mic, on_pipeline_status)
    except Exception as exc:
        error = f"{exc.__class__.__name__}: {exc}"
        with metrics_lock:
            pipeline_state["status"] = "error"
            pipeline_state["last_error"] = error
            pipeline_state["deepgram_status"] = "error"
            pipeline_state["deepgram_detail"] = error
        print(f"[pipeline:error] {error}")
        print(traceback.format_exc().rstrip())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop, ai_worker_task, ai_queue
    loop = asyncio.get_running_loop()
    ai_queue = asyncio.Queue(maxsize=AI_QUEUE_MAXSIZE)
    ai_worker_task = asyncio.create_task(ai_worker())
    if llm_reasoner.enabled:
        print(
            "AI detector enabled "
            f"({config.EXPLANATION_PROVIDER}: {llm_reasoner.model})"
        )
    else:
        print(
            "AI detector disabled "
            f"(missing credentials for {config.EXPLANATION_PROVIDER}, using dictionary fallback)"
        )

    t = threading.Thread(target=pipeline_thread, daemon=True)
    t.start()

    yield

    if ai_queue is not None:
        await ai_queue.put(None)
    if ai_worker_task is not None:
        await ai_worker_task


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
        pipeline = dict(pipeline_state)
    return {
        "uptime_seconds": int(time.time() - service_started_at),
        "connected_clients": len(clients),
        "counts": counts,
        "pipeline": pipeline,
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

    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
