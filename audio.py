import pyaudio

RATE = 16000
CHANNELS = 1
FORMAT = pyaudio.paInt16
CHUNK = 4096


def stream(callback, stop_event=None):
    """Open microphone and call callback(data) for each audio chunk.

    Blocks until stop_event is set or KeyboardInterrupt.
    """
    p = pyaudio.PyAudio()
    mic = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        frames_per_buffer=CHUNK,
    )
    try:
        while stop_event is None or not stop_event.is_set():
            data = mic.read(CHUNK, exception_on_overflow=False)
            callback(data)
    except KeyboardInterrupt:
        pass
    finally:
        mic.stop_stream()
        mic.close()
        p.terminate()
