# Common Fixes

## Realtime transcription starts but no volume / no transcript

### Symptoms
- Backend logs show mic selected and "connecting to OpenAI Realtime..."
- UI shows connected frontend websocket but audio bars stay at zero
- No interim/final transcript events appear

### Root cause
Startup could block while waiting for a strict session-ack event before consuming mic chunks.  
If that ack is delayed/missing, audio streaming never starts, so meter and transcript stay idle.

### Fix applied
- Do not hard-block startup on session ack.
- If session ack times out, continue streaming audio and let receive-loop errors surface asynchronously.
- Add explicit pipeline status logs for `connecting`, `connected`, `reconnecting`, `stopped`.

### Key files
- `openai_transcriber.py`
- `main.py`

## Tagging feels too tolerant or generation labels feel noisy

### Typical causes
- Dictionary matches are force-emitted even when contextual confidence is weak.
- Too many tags are emitted from one line.
- Preset confidence overrides local tuning unexpectedly.

### Precision-first tuning applied
- Known dictionary terms now require model-confirmed contextual usage to emit.
- Per-term fallback to dictionary is removed when the model responded but confidence/usage is weak.
- Dictionary fallback is kept only when the model call fails, preserving resilience.
- Max dictionary tags per line reduced from 3 to 2.
- `.env` `OPENAI_EXPLANATION_CONFIDENCE_THRESHOLD` now acts as a floor on preset confidence.

### Practical tuning guidance
- Increase `OPENAI_EXPLANATION_CONFIDENCE_THRESHOLD` for stricter tagging.
- Keep `OPENAI_EXPLANATION_MIN_REQUEST_SECONDS` high enough to avoid over-triggering.
- Expand/clean dictionary terms in `dictionary/*.json` for better generation accuracy.
