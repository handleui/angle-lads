# angle-lads

A personal tool that listens to real-life conversations, detects likely intergenerationally confusing slang in real time, and displays a plain-language explanation.

The Python service uses OpenAI Realtime for transcription and OpenAI Responses for background explanations.

Finalized lines are then analyzed in the background for contextual reasoning (term, definition, and why that term is confusing in context). If the configured AI explainer is not available, it falls back to local dictionary matching.
Repeated explanations are cached locally in `.angle-lads-cache.sqlite3` so common terms can resolve faster on later mentions.

The React dashboard is a localhost-only browser app that talks to the Python service's FastAPI endpoint to show a live transcript feed and a log of flagged terms.
Explanation events now include latency telemetry (`llm_roundtrip` and `final_to_explanation`) so you can tune real-time performance.

These two parts never share code — they communicate only over a local HTTP API.

## Current Scope

- OpenAI-only transcription and explanation pipeline
- Local dictionary-first definitions and transcription keyword bias
- Character portrait previews rendered directly in the UI

The old e-ink / screen-output direction is no longer part of the active plan.

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
- `ALLOWED_TRANSCRIPT_SCRIPTS` (default: `LATIN`)
- `OPENAI_REALTIME_VAD_THRESHOLD`
- `OPENAI_REALTIME_PREFIX_PADDING_MS`
- `OPENAI_REALTIME_SILENCE_MS`
- `OPENAI_REALTIME_OPTIMISTIC_FINAL_MS`
- `OPENAI_TEXT_MODEL` (default: `gpt-4.1-mini`)
- `OPENAI_TEXT_TIMEOUT_SECONDS`
- `OPENAI_MIN_REQUEST_SECONDS`

The app constrains output to Latin-script transcript text, which helps suppress accidental non-Spanish/non-English script output.
