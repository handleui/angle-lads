import random
import threading
import time

from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets.listen_v1_results_event import (
    ListenV1ResultsEvent,
)

import config

_RECONNECT_BASE_SECONDS = 0.25
_RECONNECT_MAX_SECONDS = 8.0
_FAILURE_STREAK_RESET_SECONDS = 30.0


def run(on_transcript, audio_chunks, on_status=None):
    """Stream audio to Deepgram and fire transcript events.

    on_transcript(text, is_final) — called for each transcript event.
    audio_chunks — iterator of raw audio bytes (must already be started).
    on_status(event, detail) — optional callback for connection telemetry.

    This function blocks — run it in a thread.
    """
    audio_iter = iter(audio_chunks)
    client = DeepgramClient(api_key=config.DEEPGRAM_API_KEY)
    attempts = 0
    last_failure_at = 0.0

    while True:
        try:
            _emit_status(on_status, "connecting", None)
            _stream_session(client, on_transcript, audio_iter, on_status)
            attempts = 0
        except StopIteration:
            _emit_status(on_status, "stopped", "audio stream ended")
            return
        except Exception as exc:
            now = time.monotonic()
            if not last_failure_at or (now - last_failure_at) > _FAILURE_STREAK_RESET_SECONDS:
                attempts = 0
            attempts += 1
            last_failure_at = now
            backoff = min(
                _RECONNECT_MAX_SECONDS, _RECONNECT_BASE_SECONDS * (2 ** (attempts - 1))
            )
            delay = backoff + random.uniform(0.0, 0.25)
            print(
                f"Deepgram stream dropped ({exc.__class__.__name__}). "
                f"Reconnecting in {delay:.2f}s..."
            )
            _emit_status(
                on_status,
                "reconnecting",
                f"{exc.__class__.__name__}: {exc} (retry in {delay:.2f}s)",
            )
            time.sleep(delay)


def _stream_session(client, on_transcript, audio_chunks, on_status):
    first_chunk = next(audio_chunks)
    with client.listen.v1.connect(
        model="nova-3",
        language="es",
        encoding="linear16",
        sample_rate="16000",
        channels="1",
        interim_results="true",
        smart_format="true",
        punctuate="true",
        vad_events="true",
        utterance_end_ms="1000",
        endpointing="300",
    ) as conn:
        _emit_status(on_status, "connected", None)

        def handle(message):
            if not isinstance(message, ListenV1ResultsEvent):
                return
            text = message.channel.alternatives[0].transcript
            if not text:
                return
            _emit_status(on_status, "transcript", "final" if message.is_final else "interim")
            on_transcript(text, message.is_final)

        conn.on(EventType.MESSAGE, handle)

        # start_listening() blocks forever (loops over incoming messages),
        # so run it in a background thread while we send audio here.
        listener = threading.Thread(target=conn.start_listening, daemon=True)
        listener.start()

        conn.send_media(first_chunk)
        while True:
            chunk = next(audio_chunks)
            conn.send_media(chunk)


def _emit_status(on_status, event: str, detail: str | None):
    if on_status is None:
        return
    on_status(event, detail)
