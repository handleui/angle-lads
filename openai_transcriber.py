import asyncio
import base64
import contextlib
import json
import random

from websockets.asyncio.client import connect

import config
import detector

_REALTIME_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
_RECONNECT_BASE_SECONDS = 0.25
_RECONNECT_MAX_SECONDS = 8.0
_FAILURE_STREAK_RESET_SECONDS = 30.0
_CURRENT_VAD_MODE = config.OPENAI_REALTIME_VAD_MODE
_CURRENT_VAD_EAGERNESS = config.OPENAI_REALTIME_VAD_EAGERNESS
_CURRENT_VAD_THRESHOLD = config.OPENAI_REALTIME_VAD_THRESHOLD
_CURRENT_PREFIX_PADDING_MS = config.OPENAI_REALTIME_PREFIX_PADDING_MS
_CURRENT_SILENCE_MS = config.OPENAI_REALTIME_SILENCE_MS
_CURRENT_OPTIMISTIC_FINAL_MS = config.OPENAI_REALTIME_OPTIMISTIC_FINAL_MS
_PRESETS = {
    "cafe": {
        "vad_mode": "server_vad",
        "vad_threshold": 0.7,
        "prefix_padding_ms": 140,
        "silence_ms": 260,
        "optimistic_final_ms": 0,
    },
    "privado": {
        "vad_mode": "server_vad",
        "vad_threshold": 0.58,
        "prefix_padding_ms": 200,
        "silence_ms": 340,
        "optimistic_final_ms": 0,
    },
    "focus": {
        "vad_mode": "semantic_vad",
        "vad_eagerness": "high",
        "optimistic_final_ms": 0,
    },
}


def set_preset(preset: str):
    global _CURRENT_VAD_MODE
    global _CURRENT_VAD_EAGERNESS
    global _CURRENT_VAD_THRESHOLD
    global _CURRENT_PREFIX_PADDING_MS
    global _CURRENT_SILENCE_MS
    global _CURRENT_OPTIMISTIC_FINAL_MS

    selected = _PRESETS.get(preset, _PRESETS["cafe"])
    _CURRENT_VAD_MODE = selected.get("vad_mode", config.OPENAI_REALTIME_VAD_MODE)
    _CURRENT_VAD_EAGERNESS = selected.get(
        "vad_eagerness", config.OPENAI_REALTIME_VAD_EAGERNESS
    )
    _CURRENT_VAD_THRESHOLD = selected.get(
        "vad_threshold", config.OPENAI_REALTIME_VAD_THRESHOLD
    )
    _CURRENT_PREFIX_PADDING_MS = selected.get(
        "prefix_padding_ms", config.OPENAI_REALTIME_PREFIX_PADDING_MS
    )
    _CURRENT_SILENCE_MS = selected.get("silence_ms", config.OPENAI_REALTIME_SILENCE_MS)
    _CURRENT_OPTIMISTIC_FINAL_MS = selected.get(
        "optimistic_final_ms", config.OPENAI_REALTIME_OPTIMISTIC_FINAL_MS
    )


def run(on_transcript, audio_chunks, on_status=None):
    asyncio.run(_run_forever(on_transcript, audio_chunks, on_status))


async def _run_forever(on_transcript, audio_chunks, on_status):
    audio_iter = iter(audio_chunks)
    attempts = 0
    last_failure_at = 0.0

    while True:
        try:
            await _run(on_transcript, audio_iter, on_status)
            attempts = 0
        except StopIteration:
            _emit_status(on_status, "stopped", "audio stream ended")
            return
        except Exception as exc:
            now = asyncio.get_running_loop().time()
            if (
                not last_failure_at
                or (now - last_failure_at) > _FAILURE_STREAK_RESET_SECONDS
            ):
                attempts = 0
            attempts += 1
            last_failure_at = now
            backoff = min(
                _RECONNECT_MAX_SECONDS, _RECONNECT_BASE_SECONDS * (2 ** (attempts - 1))
            )
            delay = backoff + random.uniform(0.0, 0.25)
            _emit_status(
                on_status,
                "reconnecting",
                f"{exc.__class__.__name__}: {exc} (retry in {delay:.2f}s)",
            )
            await asyncio.sleep(delay)


async def _run(on_transcript, audio_iter, on_status):
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }
    url = _REALTIME_URL
    partials: dict[str, str] = {}
    ordered_items: list[str] = []
    waiting_children: dict[str | None, list[str]] = {}
    completed_items: dict[str, dict] = {}
    emitted_items: set[str] = set()
    optimistic_tasks: dict[str, asyncio.Task] = {}
    latest_partial = {"item_id": None}

    async with connect(url, additional_headers=headers, max_size=2**24) as ws:
        _emit_status(on_status, "connecting", None)
        await ws.send(
            json.dumps(
                {
                    "type": "transcription_session.update",
                    "session": _session_config(),
                }
            )
        )
        await _await_session_ready(ws)
        _emit_status(on_status, "connected", None)

        receiver = asyncio.create_task(
            _receive_loop(
                ws,
                partials,
                ordered_items,
                waiting_children,
                completed_items,
                emitted_items,
                optimistic_tasks,
                latest_partial,
                on_transcript,
                on_status,
            )
        )
        try:
            while True:
                try:
                    chunk = await asyncio.to_thread(next, audio_iter)
                except StopIteration:
                    await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    return

                await ws.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(chunk).decode("ascii"),
                        }
                    )
                )
        finally:
            receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receiver


async def _receive_loop(
    ws,
    partials,
    ordered_items,
    waiting_children,
    completed_items,
    emitted_items,
    optimistic_tasks,
    latest_partial,
    on_transcript,
    on_status,
):
    while True:
        message = json.loads(await ws.recv())
        event_type = str(message.get("type", ""))

        if event_type == "error":
            error = message.get("error", {})
            detail = (
                error.get("message") or error.get("code") or "OpenAI realtime error"
            )
            _emit_status(on_status, "reconnecting", str(detail))
            continue

        if event_type == "input_audio_buffer.committed":
            item_id = message.get("item_id")
            previous_item_id = message.get("previous_item_id")
            if item_id:
                _insert_ordered_item(
                    item_id, previous_item_id, ordered_items, waiting_children
                )
                _flush_completed(
                    ordered_items,
                    completed_items,
                    emitted_items,
                    on_transcript,
                    on_status,
                )
            continue

        if event_type.endswith("input_audio_transcription.delta"):
            item_id = message.get("item_id") or "default"
            partials[item_id] = partials.get(item_id, "") + str(
                message.get("delta", "")
            )
            _cancel_optimistic(optimistic_tasks, item_id)
            latest_partial["item_id"] = item_id
            _emit_status(on_status, "transcript", "interim")
            on_transcript(
                partials[item_id].strip(),
                False,
                False,
                item_id,
                {"origin": "delta"},
            )
            continue

        if event_type.endswith("input_audio_transcription.completed"):
            item_id = message.get("item_id") or "default"
            _cancel_optimistic(optimistic_tasks, item_id)
            _ensure_ordered_item(item_id, ordered_items)
            text = (
                message.get("transcript")
                or message.get("text")
                or partials.pop(item_id, "")
            )
            text = str(text).strip()
            if text:
                completed_items[item_id] = {
                    "text": text,
                    "avg_logprob": _average_logprob(message.get("logprobs")),
                }
                _flush_completed(
                    ordered_items,
                    completed_items,
                    emitted_items,
                    on_transcript,
                    on_status,
                )
            continue

        if event_type == "input_audio_buffer.speech_started":
            _emit_status(on_status, "streaming", "speech_started")
            continue

        if event_type == "input_audio_buffer.speech_stopped":
            _emit_status(on_status, "streaming", "speech_stopped")
            item_id = latest_partial.get("item_id")
            if _CURRENT_OPTIMISTIC_FINAL_MS > 0 and item_id and partials.get(item_id):
                optimistic_tasks[item_id] = asyncio.create_task(
                    _emit_optimistic_final(
                        item_id,
                        partials,
                        completed_items,
                        emitted_items,
                        on_transcript,
                    )
                )


def _session_config():
    session = {
        "input_audio_format": "pcm16",
        "input_audio_noise_reduction": {"type": "near_field"},
        "input_audio_transcription": {
            "model": config.OPENAI_REALTIME_MODEL,
            "language": config.OPENAI_REALTIME_LANGUAGE,
            "prompt": _transcription_prompt(),
        },
        "turn_detection": _turn_detection_config(),
        "include": ["item.input_audio_transcription.logprobs"],
    }
    return session


def _turn_detection_config():
    if _CURRENT_VAD_MODE == "semantic_vad":
        return {
            "type": "semantic_vad",
            "eagerness": _CURRENT_VAD_EAGERNESS,
        }
    return {
        "type": "server_vad",
        "threshold": _CURRENT_VAD_THRESHOLD,
        "prefix_padding_ms": _CURRENT_PREFIX_PADDING_MS,
        "silence_duration_ms": _CURRENT_SILENCE_MS,
    }


async def _emit_optimistic_final(
    item_id: str,
    partials: dict[str, str],
    completed_items: dict[str, dict],
    emitted_items: set[str],
    on_transcript,
):
    await asyncio.sleep(_CURRENT_OPTIMISTIC_FINAL_MS / 1000)
    if item_id in emitted_items or item_id in completed_items:
        return
    text = partials.get(item_id, "").strip()
    if text:
        on_transcript(text, True, True, item_id, {"origin": "optimistic"})


def _cancel_optimistic(optimistic_tasks: dict[str, asyncio.Task], item_id: str):
    task = optimistic_tasks.pop(item_id, None)
    if task is not None:
        task.cancel()


def _insert_ordered_item(item_id, previous_item_id, ordered_items, waiting_children):
    if item_id in ordered_items:
        return
    if previous_item_id is None:
        ordered_items.insert(0, item_id)
    elif previous_item_id in ordered_items:
        ordered_items.insert(ordered_items.index(previous_item_id) + 1, item_id)
    else:
        waiting_children.setdefault(previous_item_id, []).append(item_id)
        return

    pending = waiting_children.pop(item_id, [])
    for child_id in pending:
        _insert_ordered_item(child_id, item_id, ordered_items, waiting_children)


def _ensure_ordered_item(item_id: str, ordered_items: list[str]):
    if item_id not in ordered_items:
        ordered_items.append(item_id)


def _flush_completed(
    ordered_items, completed_items, emitted_items, on_transcript, on_status
):
    for item_id in ordered_items:
        if item_id in emitted_items:
            continue
        payload = completed_items.get(item_id)
        if not payload:
            break
        emitted_items.add(item_id)
        _emit_status(on_status, "transcript", "final")
        on_transcript(
            payload["text"],
            True,
            False,
            item_id,
            {
                "origin": "completed",
                "avg_logprob": payload["avg_logprob"],
            },
        )


async def _await_session_ready(ws):
    while True:
        message = json.loads(await ws.recv())
        event_type = str(message.get("type", ""))
        if event_type in {"transcription_session.updated", "session.updated"}:
            return
        if event_type in {"transcription_session.created", "session.created"}:
            continue
        if event_type == "error":
            error = message.get("error", {})
            detail = (
                error.get("message") or error.get("code") or "OpenAI realtime error"
            )
            raise RuntimeError(str(detail))


def _emit_status(on_status, event: str, detail: str | None):
    if on_status is None:
        return
    on_status(event, detail)


def _average_logprob(logprobs) -> float | None:
    if not isinstance(logprobs, list) or not logprobs:
        return None
    values = [
        float(item["logprob"])
        for item in logprobs
        if isinstance(item, dict) and isinstance(item.get("logprob"), (int, float))
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _transcription_prompt() -> str:
    custom_prompt = config.OPENAI_REALTIME_TRANSCRIPTION_PROMPT.strip()
    hints = detector.transcription_prompt(config.OPENAI_REALTIME_HINT_TERMS)
    parts = [
        "Transcribe audio exactly as spoken.",
        "Do not translate or switch languages.",
        "Prefer Mexican Spanish and common Spanglish spellings.",
        "If audio is unclear, keep the transcript conservative instead of "
        "inventing words.",
    ]
    if custom_prompt:
        parts.append(custom_prompt)
    if hints:
        parts.append(f"Keyword hints: {hints}")
    return " ".join(parts)
