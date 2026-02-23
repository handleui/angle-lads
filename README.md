# angle-lads

A personal tool that listens to real-life conversations, detects generational slang and colloquial terms in real time, and displays a plain-language explanation on a small e-ink screen.

The Python service captures microphone audio, streams it to Deepgram for transcription, scans the transcript against slang dictionaries, generates a PNG explanation card with a pixel art character, and pushes it to the e-ink display over local WiFi.

The React dashboard is a localhost-only browser app that talks to the Python service's FastAPI endpoint to show a live transcript feed and a log of flagged terms.

These two parts never share code — they communicate only over a local HTTP API.

## Python service

```sh
pip install -r requirements.txt
python main.py
```

## React dashboard

```sh
npm install
npm run dev
```

Opens at `http://localhost:5173`. Expects the Python service to be running on `http://localhost:8000`.
