from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents

import config


def connect(on_transcript):
    """Open a Deepgram live WebSocket connection.

    on_transcript(text, is_final) is called for each transcript event.
    Returns the connection — call .send(data) to feed audio, .finish() to stop.
    """
    client = DeepgramClient(config.DEEPGRAM_API_KEY)
    connection = client.listen.websocket.v("1")

    def handle(self, result, **kwargs):
        text = result.channel.alternatives[0].transcript
        if not text:
            return
        on_transcript(text, result.is_final)

    connection.on(LiveTranscriptionEvents.Transcript, handle)

    options = LiveOptions(
        model="nova-3",
        language="en-US",
        encoding="linear16",
        sample_rate=16000,
        channels=1,
        interim_results=True,
        smart_format=True,
        punctuate=True,
    )
    connection.start(options)
    return connection
