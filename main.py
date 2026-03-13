import asyncio
import json
import math
import threading
import time
import traceback
import unicodedata
from array import array
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import audio
import config
import detector
import openai_reasoner
import openai_transcriber

RESET = "\033[0m"
GEN_COLORS = {
    "gen_z": "\033[94m",
    "millennial": "\033[95m",
    "boomer": "\033[93m",
}

clients: set[WebSocket] = set()
loop: asyncio.AbstractEventLoop | None = None
ai_worker_task: asyncio.Task | None = None
ai_queue: asyncio.Queue | None = None
pending_ai_item = None
pending_ai_lock = threading.Lock()
context_lock = threading.Lock()
CONTEXT_BUFFER_MAX = openai_reasoner.max_context_lines()
context_lines = deque(maxlen=CONTEXT_BUFFER_MAX)
llm_reasoner = openai_reasoner.OpenAIContextReasoner()
metrics_lock = threading.Lock()
service_started_at = time.time()
latency_samples = {
    "llm_roundtrip_ms": deque(maxlen=100),
    "final_to_explanation_ms": deque(maxlen=100),
}
pipeline_counts = {
    "interim_events": 0,
    "final_events": 0,
    "transcripts_dropped": 0,
    "transcripts_dropped_low_confidence": 0,
    "explanations_emitted": 0,
    "ai_jobs_enqueued": 0,
    "ai_jobs_dropped": 0,
}
pipeline_state = {
    "status": "idle",
    "last_error": None,
    "transcription_status": "idle",
    "transcription_detail": None,
    "last_event_at": None,
    "last_transcript": None,
    "last_transcript_kind": None,
    "last_drop_reason": None,
    "last_drop_text": None,
    "last_drop_avg_logprob": None,
    "audio_chunks_sent": 0,
    "audio_level": 0,
    "ai_queue_depth": 0,
    "ai_error": None,
    "transcription_provider": "openai",
    "explanation_provider": "openai",
    "preset": "cafe",
    "audio_input_device": None,
    "audio_muted": False,
}
AI_QUEUE_MAXSIZE = 8
line_id_lock = threading.Lock()
line_id_counter = 0
SPANISH_STOPWORDS = {
    "de",
    "la",
    "el",
    "que",
    "y",
    "en",
    "no",
    "se",
    "es",
    "un",
    "una",
    "por",
    "para",
    "con",
    "como",
    "pero",
    "si",
    "yo",
    "tu",
    "me",
    "te",
    "lo",
    "las",
    "los",
    "del",
    "al",
    "ya",
    "bien",
    "porque",
    "eso",
    "esta",
    "este",
}
ENGLISH_STOPWORDS = {
    "the",
    "and",
    "you",
    "that",
    "this",
    "with",
    "for",
    "not",
    "are",
    "was",
    "have",
    "just",
    "but",
    "they",
    "your",
    "out",
    "what",
    "all",
    "like",
    "can",
    "will",
    "from",
    "stay",
    "tonight",
    "okay",
}
_TRANSCRIPTION_INJECTION_MARKERS = (
    "transcribe audio exactly as spoken",
    "do not translate or switch languages",
    "prefer mexican spanish and common spanglish spellings",
    "multiple speakers may appear",
    "do not wait for perfect full sentences",
    "keep the transcript conservative instead of inventing words",
    "keyword hints:",
    "expected terms:",
)


class PresetPayload(BaseModel):
    preset: str


class MutePayload(BaseModel):
    muted: bool


def _apply_preset(preset: str) -> str:
    llm_reasoner.set_preset(preset)
    openai_transcriber.set_preset(llm_reasoner.preset)
    with metrics_lock:
        pipeline_state["preset"] = llm_reasoner.preset
    return llm_reasoner.preset


def _next_line_id() -> int:
    global line_id_counter
    with line_id_lock:
        line_id_counter += 1
        return line_id_counter


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
    line_id: int, text: str, history_snapshot: list[str], final_received_at: float
):
    llm_started_at = time.perf_counter()
    explanations = await asyncio.to_thread(
        llm_reasoner.explain_many, text, history_snapshot
    )
    llm_roundtrip_ms = int((time.perf_counter() - llm_started_at) * 1000)
    final_to_explanation_ms = int((time.perf_counter() - final_received_at) * 1000)

    if not explanations:
        with metrics_lock:
            pipeline_state["ai_error"] = llm_reasoner.last_error
        return

    avg_llm_ms = _record_latency("llm_roundtrip_ms", llm_roundtrip_ms)
    avg_e2e_ms = _record_latency("final_to_explanation_ms", final_to_explanation_ms)
    with metrics_lock:
        pipeline_counts["explanations_emitted"] += len(explanations)
        pipeline_state["ai_error"] = None

    for explanation in explanations:
        flags = _build_flags(text, explanation)
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
                "line_id": line_id,
                "text": text,
                "flags": flags,
                "timing_ms": {
                    "llm_roundtrip": llm_roundtrip_ms,
                    "final_to_explanation": final_to_explanation_ms,
                    "avg_llm_roundtrip": avg_llm_ms,
                    "avg_final_to_explanation": avg_e2e_ms,
                },
                **explanation,
            },
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
            line_id, text, history_snapshot, final_received_at = item
            await analyze_and_broadcast(
                line_id, text, history_snapshot, final_received_at
            )
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


def on_transcript(
    text: str,
    is_final: bool,
    provisional: bool = False,
    source_id: str | None = None,
    metadata: dict | None = None,
):
    global pending_ai_item
    if loop is None:
        return

    received_at = time.perf_counter()
    text = text.strip()
    drop_reason = _drop_reason_for_transcript(text, is_final, provisional, metadata)
    if drop_reason is not None:
        with metrics_lock:
            pipeline_counts["transcripts_dropped"] += 1
            if drop_reason == "low_confidence":
                pipeline_counts["transcripts_dropped_low_confidence"] += 1
            pipeline_state["last_drop_reason"] = drop_reason
            pipeline_state["last_drop_text"] = text[-160:] if text else None
            pipeline_state["last_drop_avg_logprob"] = (
                round(float(metadata["avg_logprob"]), 3)
                if drop_reason == "low_confidence"
                and metadata
                and isinstance(metadata.get("avg_logprob"), (int, float))
                else None
            )
        return
    with metrics_lock:
        key = "final_events" if is_final else "interim_events"
        pipeline_counts[key] += 1

    flags = []
    history_snapshot: list[str] = []

    if is_final and not provisional:
        if llm_reasoner.enabled:
            with context_lock:
                context_lines.append(text)
                history_snapshot = list(context_lines)[-llm_reasoner.context_lines :]
        else:
            flags = detector.scan(text)

    for flag in flags:
        color = GEN_COLORS.get(flag["generation"], "")
        print(
            f"{color}[{flag['generation']}]{RESET} "
            f"{flag['term']} → {flag['definition']}"
        )

    line_id = _next_line_id() if is_final else None
    msg = {
        "type": "final" if is_final else "interim",
        "id": line_id,
        "source_id": source_id,
        "provisional": provisional,
        "text": text,
        "flags": flags,
    }
    with metrics_lock:
        pipeline_state["last_event_at"] = time.time()
        pipeline_state["last_transcript"] = text[-160:]
        pipeline_state["last_transcript_kind"] = "final" if is_final else "interim"
    asyncio.run_coroutine_threadsafe(broadcast(msg), loop)

    if is_final and not provisional and llm_reasoner.enabled:
        item = (line_id, text, history_snapshot, received_at)
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


def _drop_reason_for_transcript(
    text: str,
    is_final: bool,
    provisional: bool,
    metadata: dict | None,
) -> str | None:
    if not text:
        return "empty"
    if _is_low_confidence_transcript(text, is_final, provisional, metadata):
        return "low_confidence"
    if _contains_disallowed_script(text):
        return "disallowed_script"
    if _looks_like_dictionary_dump(text):
        return "dictionary_dump"
    if _looks_like_transcription_instruction_leak(text):
        return "instruction_leak"
    if _looks_unsupported_language(text):
        return "unsupported_language"
    return None


def _is_low_confidence_transcript(
    text: str,
    is_final: bool,
    provisional: bool,
    metadata: dict | None,
) -> bool:
    if provisional:
        return False
    if not is_final or not metadata:
        return False
    avg_logprob = metadata.get("avg_logprob")
    if not isinstance(avg_logprob, (int, float)):
        return False
    if avg_logprob >= config.OPENAI_REALTIME_MIN_AVG_LOGPROB:
        return False
    severe_cutoff = config.OPENAI_REALTIME_MIN_AVG_LOGPROB - 0.45
    word_count = len(text.split())
    if avg_logprob > severe_cutoff and (word_count >= 4 or len(text) >= 24):
        return False
    print(f"[transcript:drop] low confidence avg_logprob={avg_logprob:.2f} :: {text}")
    return True


def on_pipeline_status(event: str, detail: str | None):
    with metrics_lock:
        pipeline_state["last_event_at"] = time.time()

        if event == "connected":
            pipeline_state["status"] = "running"
            pipeline_state["transcription_status"] = "connected"
            pipeline_state["transcription_detail"] = None
            return

        if event == "connecting":
            pipeline_state["status"] = "starting"
            pipeline_state["transcription_status"] = "connecting"
            pipeline_state["transcription_detail"] = detail
            return

        if event == "reconnecting":
            pipeline_state["status"] = "degraded"
            pipeline_state["transcription_status"] = "reconnecting"
            pipeline_state["transcription_detail"] = detail
            return

        if event == "stopped":
            pipeline_state["status"] = "stopped"
            pipeline_state["transcription_status"] = "stopped"
            pipeline_state["transcription_detail"] = detail
            return

        if event == "transcript":
            pipeline_state["transcription_status"] = "streaming"
            pipeline_state["transcription_detail"] = detail


def tracked_audio_stream(rate: int, chunk_size: int):
    for chunk in audio.stream(rate=rate, chunk=chunk_size):
        samples = array("h")
        samples.frombytes(chunk)
        peak = max((abs(sample) for sample in samples), default=0)
        rms = (
            math.sqrt(sum(sample * sample for sample in samples) / len(samples))
            if samples
            else 0.0
        )
        peak_level = (peak / 32767) ** 0.35 if peak else 0.0
        rms_level = (rms / 32767) ** 0.3 if rms else 0.0
        normalized_level = min(100, int(max(peak_level, rms_level) * 100))
        with metrics_lock:
            muted = bool(pipeline_state["audio_muted"])
            pipeline_state["audio_chunks_sent"] += 1
            pipeline_state["last_event_at"] = time.time()
            previous_level = pipeline_state["audio_level"]
            target_level = 0 if muted else normalized_level
            if target_level >= previous_level:
                smoothed_level = int((previous_level * 0.4) + (target_level * 0.6))
            else:
                # Slightly faster decay when muted so UI reflects silence quickly.
                decay = 0.34 if muted else 0.18
                smoothed_level = int(
                    (previous_level * (1.0 - decay)) + (target_level * decay)
                )
            pipeline_state["audio_level"] = max(0, min(100, smoothed_level))
        yield bytes(len(chunk)) if muted else chunk


def _build_flags(text: str, explanation: dict) -> list[dict]:
    term = str(explanation.get("term", "")).strip()
    definition = str(explanation.get("definition", "")).strip()
    generation = str(explanation.get("target_generation", "unknown")).strip()
    if not term or not definition:
        return []

    flags = []
    for start, end in _find_term_spans(text, term):
        flags.append(
            {
                "term": term,
                "definition": definition,
                "generation": generation,
                "start": start,
                "end": end,
            }
        )
    return flags


def _find_term_spans(text: str, term: str) -> list[tuple[int, int]]:
    normalized_text, text_map = _normalize_with_map(text)
    normalized_term, _ = _normalize_with_map(term)
    if not normalized_text or not normalized_term:
        return []

    spans = []
    cursor = 0
    while True:
        index = normalized_text.find(normalized_term, cursor)
        if index < 0:
            break
        end_index = index + len(normalized_term)
        left_ok = index == 0 or not normalized_text[index - 1].isalnum()
        right_ok = (
            end_index >= len(normalized_text)
            or not normalized_text[end_index].isalnum()
        )
        if left_ok and right_ok:
            start = text_map[index]
            end = text_map[end_index - 1] + 1
            spans.append((start, end))
        cursor = index + len(normalized_term)
    return spans


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    normalized_chars: list[str] = []
    index_map: list[int] = []
    for index, char in enumerate(text):
        folded = unicodedata.normalize("NFKD", char)
        folded = "".join(part for part in folded if not unicodedata.combining(part))
        folded = folded.lower()
        for part in folded:
            normalized_chars.append(part)
            index_map.append(index)
    return "".join(normalized_chars), index_map


def _contains_disallowed_script(text: str) -> bool:
    letter_count = 0
    allowed_count = 0
    disallowed_count = 0
    for char in text:
        if not char.isalpha():
            continue
        letter_count += 1
        char_name = unicodedata.name(char, "")
        if any(script in char_name for script in config.ALLOWED_TRANSCRIPT_SCRIPTS):
            allowed_count += 1
        else:
            disallowed_count += 1
    if letter_count == 0:
        return False
    if allowed_count == 0:
        return True
    if disallowed_count >= 2:
        return True
    return (disallowed_count / letter_count) >= 0.2


def _looks_like_dictionary_dump(text: str) -> bool:
    matches = detector.scan(text)
    if len(matches) < 6:
        return False

    distinct_terms = {match["term"] for match in matches}
    comma_count = text.count(",")
    covered_chars = sum(match["end"] - match["start"] for match in matches)
    coverage = covered_chars / max(len(text), 1)

    if len(distinct_terms) >= 6 and comma_count >= 4:
        return True
    if len(distinct_terms) >= 5 and coverage >= 0.45:
        return True
    return False


def _looks_unsupported_language(text: str) -> bool:
    tokens = [
        token
        for token in (
            "".join(char for char in word.lower() if char.isalpha())
            for word in text.split()
        )
        if token
    ]
    if len(tokens) < 4:
        return False
    if detector.scan(text):
        return False

    support = sum(
        1
        for token in tokens
        if token in SPANISH_STOPWORDS or token in ENGLISH_STOPWORDS
    )
    return support == 0


def _looks_like_transcription_instruction_leak(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    if len(normalized) < 40:
        return False
    matches = sum(
        1 for marker in _TRANSCRIPTION_INJECTION_MARKERS if marker in normalized
    )
    if matches >= 2:
        return True
    if "keyword hints:" in normalized or "expected terms:" in normalized:
        return True
    if (
        "transcribe audio exactly as spoken" in normalized
        and "do not translate" in normalized
    ):
        return True
    return False


def pipeline_thread():
    """Run mic → OpenAI Realtime transcription in a blocking thread."""
    with metrics_lock:
        pipeline_state["status"] = "starting"
        pipeline_state["last_error"] = None
        pipeline_state["transcription_status"] = "starting"
        pipeline_state["transcription_detail"] = None
        pipeline_state["last_event_at"] = time.time()
        pipeline_state["last_transcript"] = None
        pipeline_state["last_transcript_kind"] = None
        pipeline_state["last_drop_reason"] = None
        pipeline_state["last_drop_text"] = None
        pipeline_state["last_drop_avg_logprob"] = None
        pipeline_state["audio_chunks_sent"] = 0
        pipeline_state["audio_level"] = 0
        pipeline_state["ai_queue_depth"] = 0
        pipeline_state["ai_error"] = None
        pipeline_state["audio_input_device"] = None
        pipeline_state["audio_muted"] = False

    try:
        print("Opening microphone…")
        sample_rate = config.OPENAI_AUDIO_RATE
        chunk_size = config.OPENAI_AUDIO_CHUNK
        runner = openai_transcriber.run
        device = audio.input_device_info()
        with metrics_lock:
            pipeline_state["audio_input_device"] = device
        print(
            "Mic selected: "
            f"{device['name']} (index {device['index']}, "
            f"default {device['default_sample_rate']} Hz)"
        )
        print("Mic ready, connecting to OpenAI Realtime…")
        mic = tracked_audio_stream(sample_rate, chunk_size)
        with metrics_lock:
            pipeline_state["status"] = "running"
            pipeline_state["transcription_status"] = "connecting"
        runner(on_transcript, mic, on_pipeline_status)
    except Exception as exc:
        error = f"{exc.__class__.__name__}: {exc}"
        with metrics_lock:
            pipeline_state["status"] = "error"
            pipeline_state["last_error"] = error
            pipeline_state["transcription_status"] = "error"
            pipeline_state["transcription_detail"] = error
        print(f"[pipeline:error] {error}")
        print(traceback.format_exc().rstrip())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop, ai_worker_task, ai_queue
    loop = asyncio.get_running_loop()
    ai_queue = asyncio.Queue(maxsize=AI_QUEUE_MAXSIZE)
    ai_worker_task = asyncio.create_task(ai_worker())
    _apply_preset("cafe")
    if llm_reasoner.enabled:
        print(f"AI detector enabled (openai: {llm_reasoner.model})")
    else:
        print(
            "AI detector disabled "
            "(missing OpenAI credentials, using dictionary fallback)"
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


@app.post("/preset")
async def set_preset(payload: PresetPayload):
    preset = payload.preset.strip().lower()
    return {"preset": _apply_preset(preset)}


@app.post("/mute")
async def set_mute(payload: MutePayload):
    with metrics_lock:
        pipeline_state["audio_muted"] = bool(payload.muted)
        if pipeline_state["audio_muted"]:
            pipeline_state["audio_level"] = 0
    return {"muted": bool(payload.muted)}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    try:
        while True:
            message = await websocket.receive_text()
            if message == '{"type":"ping"}':
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        clients.discard(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
