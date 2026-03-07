import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import config

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_TERM_RE = re.compile(r"\s+")
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_BLOCKING_FINISH_REASONS = {
    "SAFETY",
    "BLOCKLIST",
    "PROHIBITED_CONTENT",
    "RECITATION",
}
_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "required": [
        "should_flag",
        "term",
        "definition",
        "why_in_context",
        "target_generation",
        "confidence",
    ],
    "properties": {
        "should_flag": {"type": "BOOLEAN"},
        "term": {"type": "STRING"},
        "definition": {"type": "STRING"},
        "why_in_context": {"type": "STRING"},
        "target_generation": {
            "type": "STRING",
            "enum": ["boomer", "gen_x", "millennial", "gen_z", "mixed", "unknown"],
        },
        "confidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
    },
}


class ContextReasoner:
    def __init__(self):
        self.api_key = config.GEMINI_API_KEY
        self.model = config.GEMINI_MODEL
        self.timeout_seconds = config.GEMINI_TIMEOUT_SECONDS
        self.min_confidence = config.GEMINI_CONFIDENCE_THRESHOLD
        self.cooldown_seconds = config.GEMINI_TERM_COOLDOWN_SECONDS
        self.max_retries = max(0, config.GEMINI_MAX_RETRIES)
        self.retry_base_seconds = max(0.05, config.GEMINI_RETRY_BASE_SECONDS)
        self.enabled = bool(self.api_key)
        self._last_fired_at: dict[str, float] = {}

    def explain(self, latest_line: str, history: list[str]) -> dict | None:
        if not self.enabled:
            return None

        text = self._generate_json(latest_line, history)
        if text is None:
            return None

        parsed = self._parse_json(text)
        if parsed is None or not parsed.get("should_flag"):
            return None

        term = str(parsed.get("term", "")).strip()
        definition = str(parsed.get("definition", "")).strip()
        why = str(parsed.get("why_in_context", "")).strip()
        target_generation = str(parsed.get("target_generation", "unknown")).strip()
        confidence = _to_float(parsed.get("confidence"))

        if not term or not definition or not why:
            return None
        if confidence < self.min_confidence:
            return None
        if not self._passes_cooldown(term):
            return None

        return {
            "term": term,
            "definition": definition,
            "why_in_context": why,
            "target_generation": target_generation,
            "confidence": confidence,
            "model": self.model,
        }

    def _passes_cooldown(self, term: str) -> bool:
        now = time.monotonic()
        normalized = _normalize_term(term)
        last_seen = self._last_fired_at.get(normalized, 0.0)
        if now - last_seen < self.cooldown_seconds:
            return False
        self._last_fired_at[normalized] = now
        return True

    def _generate_json(self, latest_line: str, history: list[str]) -> str | None:
        history_slice = history[-config.GEMINI_CONTEXT_LINES :]
        history_text = "\n".join(f"- {line}" for line in history_slice)
        prompt = (
            "You are a sociolinguistic interpreter for Mexican Spanish and Spanglish.\n"
            "Decide if the latest utterance likely includes a term that another "
            "generation (especially older listeners) may not understand in this "
            "conversation context.\n\n"
            "Return ONLY valid JSON with this exact shape:\n"
            "{\n"
            '  "should_flag": boolean,\n'
            '  "term": string,\n'
            '  "definition": string,\n'
            '  "why_in_context": string,\n'
            '  "target_generation": "boomer" | "gen_x" | "millennial" | '
            '"gen_z" | "mixed" | "unknown",\n'
            '  "confidence": number\n'
            "}\n\n"
            "Rules:\n"
            "- If nothing is likely confusing, set should_flag=false and keep "
            "other fields concise.\n"
            "- Use plain Spanish for the definition and explanation.\n"
            "- explanation must reference recent context, not just dictionary "
            "meaning.\n"
            "- confidence must be between 0 and 1.\n\n"
            f"Recent conversation (oldest to newest):\n{history_text}\n\n"
            f"Latest utterance:\n{latest_line}\n"
        )

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 220,
                "responseMimeType": "application/json",
                "responseSchema": _RESPONSE_SCHEMA,
            },
        }
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{urllib.parse.quote(self.model, safe='')}:generateContent"
            f"?key={urllib.parse.quote(self.api_key, safe='')}"
        )
        data = json.dumps(payload).encode("utf-8")
        decoded = self._request_with_retry(endpoint, data)
        if decoded is None:
            return None

        if decoded.get("promptFeedback", {}).get("blockReason"):
            return None

        candidates = decoded.get("candidates", [])
        if not candidates:
            return None

        first_candidate = candidates[0]
        finish_reason = str(first_candidate.get("finishReason", "")).upper()
        if finish_reason in _BLOCKING_FINISH_REASONS:
            return None

        parts = first_candidate.get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        return text.strip() or None

    def _request_with_retry(self, endpoint: str, data: bytes) -> dict | None:
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(
                endpoint, data=data, headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as res:
                    return json.loads(res.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if (
                    exc.code not in _RETRYABLE_STATUS_CODES
                    or attempt >= self.max_retries
                ):
                    return None
            except (TimeoutError, urllib.error.URLError, json.JSONDecodeError):
                if attempt >= self.max_retries:
                    return None

            delay = (self.retry_base_seconds * (2**attempt)) + random.uniform(
                0.0, self.retry_base_seconds
            )
            time.sleep(delay)

        return None

    def _parse_json(self, text: str) -> dict | None:
        normalized = text.strip()
        if normalized.startswith("```"):
            normalized = re.sub(r"^```(?:json)?\s*", "", normalized)
            normalized = re.sub(r"\s*```$", "", normalized)

        match = _JSON_BLOCK_RE.search(normalized)
        if match:
            normalized = match.group(0)

        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_term(term: str) -> str:
    compact = _TERM_RE.sub(" ", term.strip().lower())
    return compact
