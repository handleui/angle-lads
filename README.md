# angle-lads

A personal tool that listens to real-life conversations, detects likely intergenerationally confusing slang in real time, and displays a plain-language explanation.

The Python service uses OpenAI Realtime for transcription and OpenAI Responses for background explanations.

Finalized lines are then analyzed in the background with OpenAI Responses for contextual reasoning. If OpenAI explanation is unavailable, it falls back to local dictionary matching.
Local dictionary entries are cached in `.angle-lads-cache.sqlite3` for fast repeated lookups without replaying old AI context.

The React dashboard is a localhost-only browser app that talks to the Python service's FastAPI endpoint to show a live transcript feed and a log of flagged terms.
Explanation events now include latency telemetry (`llm_roundtrip` and `final_to_explanation`) so you can tune real-time performance.

These two parts never share code — they communicate only over a local HTTP API.

## Current Scope

- OpenAI-only transcription and explanation pipeline
- Local dictionary-first definitions and transcription keyword bias
- Character portrait previews rendered directly in the UI

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

## Interface

- Live transcript with clickable highlighted terms
- Context panel with per-generation portrait previews
- Mobile bottom sheet layout for definitions and context
- Preset switching in the UI for `cafe`, `privado`, and `focus`, affecting both transcription and explanation behavior

## Environment

Copy `.env.example` to `.env` and add `OPENAI_API_KEY`.

Optional tuning:

- `OPENAI_REALTIME_MODEL` (default: `gpt-4o-mini-transcribe`)
- `OPENAI_REALTIME_LANGUAGE`
- `OPENAI_REALTIME_TRANSCRIPTION_PROMPT`
- `OPENAI_REALTIME_HINT_TERMS`
- `OPENAI_REALTIME_MIN_AVG_LOGPROB`
- `ALLOWED_TRANSCRIPT_SCRIPTS` (default: `LATIN`)
- `OPENAI_REALTIME_VAD_THRESHOLD`
- `OPENAI_REALTIME_PREFIX_PADDING_MS`
- `OPENAI_REALTIME_SILENCE_MS`
- `OPENAI_REALTIME_OPTIMISTIC_FINAL_MS`
- `OPENAI_EXPLANATION_MODEL` (default: `gpt-4.1-mini`)
- `OPENAI_EXPLANATION_TIMEOUT_SECONDS`
- `OPENAI_EXPLANATION_CONTEXT_LINES`
- `OPENAI_EXPLANATION_CONFIDENCE_THRESHOLD`
- `OPENAI_EXPLANATION_TERM_COOLDOWN_SECONDS`
- `OPENAI_EXPLANATION_MIN_REQUEST_SECONDS`
- `OPENAI_EXPLANATION_MAX_RETRIES`
- `OPENAI_EXPLANATION_RETRY_BASE_SECONDS`

The app constrains output to Latin-script transcript text, which helps suppress accidental non-Spanish/non-English script output.
