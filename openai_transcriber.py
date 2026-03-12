import asyncio
import base64
import contextlib
import json
import random

from websockets.asyncio.client import connect

import config

_REALTIME_URL = "wss://api.openai.com/v1/realtime"
_RECONNECT_BASE_SECONDS = 0.25
_RECONNECT_MAX_SECONDS = 8.0
_FAILURE_STREAK_RESET_SECONDS = 30.0


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
            if not last_failure_at or (now - last_failure_at) > _FAILURE_STREAK_RESET_SECONDS:
                attempts = 0
            attempts += 1
            last_failure_at = now
            backoff = min(_RECONNECT_MAX_SECONDS, _RECONNECT_BASE_SECONDS * (2 ** (attempts - 1)))
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
    url = f"{_REALTIME_URL}?model={config.OPENAI_REALTIME_SESSION_MODEL}"
    partials: dict[str, str] = {}

    async with connect(url, additional_headers=headers, max_size=2**24) as ws:
        _emit_status(on_status, "connecting", None)
        await ws.send(json.dumps({"type": "session.update", "session": _session_config()}))
        await _await_session_ready(ws)
        _emit_status(on_status, "connected", None)

        receiver = asyncio.create_task(_receive_loop(ws, partials, on_transcript, on_status))
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


async def _receive_loop(ws, partials, on_transcript, on_status):
    while True:
        message = json.loads(await ws.recv())
        event_type = str(message.get("type", ""))

        if event_type == "error":
            error = message.get("error", {})
            detail = error.get("message") or error.get("code") or "OpenAI realtime error"
            _emit_status(on_status, "reconnecting", str(detail))
            continue

        if event_type.endswith("input_audio_transcription.delta"):
            item_id = message.get("item_id") or "default"
            partials[item_id] = partials.get(item_id, "") + str(message.get("delta", ""))
            _emit_status(on_status, "transcript", "interim")
            on_transcript(partials[item_id].strip(), False)
            continue

        if event_type.endswith("input_audio_transcription.completed"):
            item_id = message.get("item_id") or "default"
            text = (
                message.get("transcript")
                or message.get("text")
                or partials.pop(item_id, "")
            )
            text = str(text).strip()
            if text:
                _emit_status(on_status, "transcript", "final")
                on_transcript(text, True)
            continue

        if event_type == "input_audio_buffer.speech_started":
            _emit_status(on_status, "streaming", "speech_started")
            continue

        if event_type == "input_audio_buffer.speech_stopped":
            _emit_status(on_status, "streaming", "speech_stopped")


def _session_config():
    return {
        "input_audio_format": "pcm16",
        "input_audio_noise_reduction": {"type": "near_field"},
        "input_audio_transcription": {
            "model": config.OPENAI_REALTIME_MODEL,
            "language": config.OPENAI_REALTIME_LANGUAGE,
            "prompt": config.OPENAI_REALTIME_PROMPT,
        },
        "turn_detection": {
            "type": "server_vad",
            "threshold": config.OPENAI_REALTIME_VAD_THRESHOLD,
            "prefix_padding_ms": config.OPENAI_REALTIME_PREFIX_PADDING_MS,
            "silence_duration_ms": config.OPENAI_REALTIME_SILENCE_MS,
            "create_response": config.OPENAI_REALTIME_CREATE_RESPONSE,
        },
    }


async def _await_session_ready(ws):
    while True:
        message = json.loads(await ws.recv())
        event_type = str(message.get("type", ""))
        if event_type == "session.updated":
            return
        if event_type == "session.created":
            continue
        if event_type == "error":
            error = message.get("error", {})
            detail = error.get("message") or error.get("code") or "OpenAI realtime error"
            raise RuntimeError(str(detail))


def _emit_status(on_status, event: str, detail: str | None):
    if on_status is None:
        return
    on_status(event, detail)
