# angle-lads

A personal tool that listens to real-life conversations, detects likely intergenerationally confusing slang in real time, and displays a plain-language explanation.

The Python service captures microphone audio and can run in two modes:

- `deepgram + gemini` for the original pipeline
- `openai realtime + openai responses` for the v2 pipeline

Finalized lines are then analyzed in the background for contextual reasoning (term, definition, and why that term is confusing in context). If the configured AI explainer is not available, it falls back to local dictionary matching.

The React dashboard is a localhost-only browser app that talks to the Python service's FastAPI endpoint to show a live transcript feed and a log of flagged terms.
Explanation events now include latency telemetry (`llm_roundtrip` and `final_to_explanation`) so you can tune real-time performance.

These two parts never share code — they communicate only over a local HTTP API.

## Setup

```sh
brew install uv just ruff portaudio
just setup
```

## Development

```sh
just dev      # runs both Python backend and Vite dev server
just py       # Python backend only
just web      # Vite dev server only
just lint     # lint everything (ruff + biome)
just fmt      # format everything (ruff + biome)
```

Dashboard opens at `http://localhost:5173`. Python service runs on `http://localhost:8000`.
Latency and counters are available at `http://localhost:8000/metrics`.

## Environment

Copy `.env.example` to `.env` and add the keys needed for your chosen mode.

V1:

- `TRANSCRIPTION_PROVIDER=deepgram`
- `EXPLANATION_PROVIDER=gemini`
- `DEEPGRAM_API_KEY`
- `GEMINI_API_KEY`

V2:

- `TRANSCRIPTION_PROVIDER=openai`
- `EXPLANATION_PROVIDER=openai`
- `OPENAI_API_KEY`

Optional tuning:

- `GEMINI_MODEL` (default: `gemini-2.5-flash`)
- `GEMINI_MIN_REQUEST_SECONDS`
- `GEMINI_TIMEOUT_SECONDS`
- `GEMINI_CONTEXT_LINES`
- `GEMINI_CONFIDENCE_THRESHOLD`
- `GEMINI_TERM_COOLDOWN_SECONDS`
- `GEMINI_MAX_RETRIES`
- `GEMINI_RETRY_BASE_SECONDS`
- `OPENAI_REALTIME_MODEL` (default: `gpt-4o-mini-transcribe`)
- `OPENAI_REALTIME_LANGUAGE`
- `OPENAI_REALTIME_PROMPT`
- `OPENAI_REALTIME_VAD_THRESHOLD`
- `OPENAI_REALTIME_PREFIX_PADDING_MS`
- `OPENAI_REALTIME_SILENCE_MS`
- `OPENAI_TEXT_MODEL` (default: `gpt-4.1-mini`)
- `OPENAI_TEXT_TIMEOUT_SECONDS`
- `OPENAI_MIN_REQUEST_SECONDS`
