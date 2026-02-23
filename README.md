# angle-lads

A personal tool that listens to real-life conversations, detects generational slang and colloquial terms in real time, and displays a plain-language explanation on a small e-ink screen.

The Python service captures microphone audio, streams it to Deepgram for word-by-word transcription, scans the transcript against slang dictionaries, generates a PNG explanation card with a pixel art character, and pushes it to the e-ink display over local WiFi.

The React dashboard is a localhost-only browser app that talks to the Python service's FastAPI endpoint to show a live transcript feed and a log of flagged terms.

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

## Environment

Copy `.env.example` to `.env` and add your Deepgram API key.
