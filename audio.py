import pyaudio

RATE = 16000
CHANNELS = 1
FORMAT = pyaudio.paInt16
CHUNK = 4096


def stream(rate: int = RATE, chunk: int = CHUNK):
    """Open microphone and yield raw audio chunks.

    Yields bytes until KeyboardInterrupt.
    """
    p = pyaudio.PyAudio()
    mic = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=rate,
        input=True,
        frames_per_buffer=chunk,
    )
    try:
        while True:
            yield mic.read(chunk, exception_on_overflow=False)
    except KeyboardInterrupt:
        pass
    finally:
        mic.stop_stream()
        mic.close()
        p.terminate()
