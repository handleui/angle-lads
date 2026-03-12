import pyaudio

import config

RATE = 16000
CHANNELS = 1
FORMAT = pyaudio.paInt16
CHUNK = 4096


def input_device_info() -> dict:
    p = pyaudio.PyAudio()
    try:
        device_index = _select_input_device_index(p)
        info = p.get_device_info_by_index(device_index)
        return {
            "index": int(info["index"]),
            "name": str(info["name"]),
            "default_sample_rate": int(info.get("defaultSampleRate", 0)),
            "max_input_channels": int(info.get("maxInputChannels", 0)),
        }
    finally:
        p.terminate()


def stream(rate: int = RATE, chunk: int = CHUNK):
    """Open microphone and yield raw audio chunks.

    Yields bytes until KeyboardInterrupt.
    """
    p = pyaudio.PyAudio()
    device_index = _select_input_device_index(p)
    mic = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=rate,
        input=True,
        input_device_index=device_index,
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


def _select_input_device_index(p: pyaudio.PyAudio) -> int:
    if config.AUDIO_INPUT_DEVICE_INDEX is not None:
        return config.AUDIO_INPUT_DEVICE_INDEX

    requested_name = config.AUDIO_INPUT_DEVICE_NAME.casefold()
    if requested_name:
        for index in range(p.get_device_count()):
            info = p.get_device_info_by_index(index)
            if int(info.get("maxInputChannels", 0)) <= 0:
                continue
            if requested_name in str(info.get("name", "")).casefold():
                return int(info["index"])
        raise RuntimeError(
            f"Audio input device not found: {config.AUDIO_INPUT_DEVICE_NAME}"
        )

    default_info = p.get_default_input_device_info()
    return int(default_info["index"])
