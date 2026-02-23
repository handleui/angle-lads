import pyaudio

RATE = 16000
CHANNELS = 1
FORMAT = pyaudio.paInt16
CHUNK = 4096


def stream():
    """Open microphone and yield raw audio chunks.

    Yields bytes until KeyboardInterrupt.
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
        while True:
            yield mic.read(CHUNK, exception_on_overflow=False)
    except KeyboardInterrupt:
        pass
    finally:
        mic.stop_stream()
        mic.close()
        p.terminate()
