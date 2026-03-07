# angle-lads

A personal tool that listens to real-life conversations, detects likely intergenerationally confusing slang in real time, and displays a plain-language explanation.

The Python service captures microphone audio, streams it to Deepgram for word-by-word transcription, and sends finalized lines to Gemini 3 Flash for contextual reasoning (term, definition, and why that term is confusing in context). If Gemini is not configured, it falls back to local dictionary matching.

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

Copy `.env.example` to `.env` and add:

- `DEEPGRAM_API_KEY`
- `GEMINI_API_KEY`

Optional tuning:

- `GEMINI_MODEL` (default: `gemini-3-flash-preview`)
- `GEMINI_TIMEOUT_SECONDS`
- `GEMINI_CONTEXT_LINES`
- `GEMINI_CONFIDENCE_THRESHOLD`
- `GEMINI_TERM_COOLDOWN_SECONDS`
- `GEMINI_MAX_RETRIES`
- `GEMINI_RETRY_BASE_SECONDS`
