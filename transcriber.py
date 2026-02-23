from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets.listen_v1_results_event import (
    ListenV1ResultsEvent,
)

import config


def run(on_transcript, get_audio):
    """Open Deepgram live connection, stream audio, and fire transcript events.

    on_transcript(text, is_final) — called for each transcript event.
    get_audio() — callable that yields audio bytes (blocks until data is ready).

    This function blocks — run it in a thread.
    """
    client = DeepgramClient(api_key=config.DEEPGRAM_API_KEY)

    with client.listen.v1.connect(
        model="nova-3",
        language="es",
        encoding="linear16",
        sample_rate="16000",
        channels="1",
        interim_results="true",
        smart_format="true",
        punctuate="true",
    ) as conn:

        def handle(message):
            if not isinstance(message, ListenV1ResultsEvent):
                return
            text = message.channel.alternatives[0].transcript
            if not text:
                return
            on_transcript(text, message.is_final)

        conn.on(EventType.MESSAGE, handle)
        conn.start_listening()

        for chunk in get_audio():
            conn.send_media(chunk)
