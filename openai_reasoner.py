import json
import random
import re
import threading
import time
import urllib.error
import urllib.request

import config

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_TERM_RE = re.compile(r"\s+")
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class OpenAIContextReasoner:
    def __init__(self):
        self.api_key = config.OPENAI_API_KEY
        self.model = config.OPENAI_TEXT_MODEL
        self.timeout_seconds = config.OPENAI_TEXT_TIMEOUT_SECONDS
        self.min_confidence = config.GEMINI_CONFIDENCE_THRESHOLD
        self.cooldown_seconds = config.GEMINI_TERM_COOLDOWN_SECONDS
        self.min_request_seconds = max(0.0, config.OPENAI_MIN_REQUEST_SECONDS)
        self.max_retries = max(0, config.GEMINI_MAX_RETRIES)
        self.retry_base_seconds = max(0.05, config.GEMINI_RETRY_BASE_SECONDS)
        self.enabled = bool(self.api_key)
        self.last_error: str | None = None
        self._last_fired_at: dict[str, float] = {}
        self._cooldown_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._last_request_started_at = 0.0

    def explain(self, latest_line: str, history: list[str]) -> dict | None:
        if not self.enabled:
            return None

        self.last_error = None
        if not self._wait_for_request_slot():
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

    def _wait_for_request_slot(self) -> bool:
        with self._request_lock:
            now = time.monotonic()
            wait_seconds = self.min_request_seconds - (now - self._last_request_started_at)
            if wait_seconds > 0:
                self.last_error = f"IA en enfriamiento ({wait_seconds:.1f}s)"
                return False
            self._last_request_started_at = now
            return True

    def _passes_cooldown(self, term: str) -> bool:
        now = time.monotonic()
        normalized = _normalize_term(term)
        with self._cooldown_lock:
            last_seen = self._last_fired_at.get(normalized, 0.0)
            if now - last_seen < self.cooldown_seconds:
                return False
            self._last_fired_at[normalized] = now
            return True

    def _generate_json(self, latest_line: str, history: list[str]) -> str | None:
        history_text = "\n".join(f"- {line}" for line in history[-config.GEMINI_CONTEXT_LINES :])
        instructions = (
            "Eres un interprete sociolinguistico de espanol mexicano y spanglish. "
            "Debes detectar si la ultima frase contiene un termino que otra generacion "
            "podria no entender en este contexto. Responde SOLO JSON valido con esta forma exacta: "
            '{"should_flag": boolean, "term": string, "definition": string, '
            '"why_in_context": string, "target_generation": "boomer" | "gen_x" | "millennial" | '
            '"gen_z" | "mixed" | "unknown", "confidence": number}. '
            "Si no hay termino confuso, usa should_flag=false y manten lo demas breve. "
            "Usa espanol claro en definition y why_in_context."
        )
        prompt = (
            f"Conversacion reciente:\n{history_text}\n\n"
            f"Ultima frase:\n{latest_line}\n"
        )
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": prompt,
            "store": False,
            "temperature": 0.1,
            "max_output_tokens": 220,
        }
        data = json.dumps(payload).encode("utf-8")
        decoded = self._request_with_retry(data)
        if decoded is None:
            return None
        return _extract_output_text(decoded)

    def _request_with_retry(self, data: bytes) -> dict | None:
        endpoint = "https://api.openai.com/v1/responses"
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(
                endpoint,
                data=data,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as res:
                    self.last_error = None
                    return json.loads(res.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                self.last_error = f"OpenAI HTTP {exc.code}"
                if exc.code not in _RETRYABLE_STATUS_CODES or attempt >= self.max_retries:
                    return None
            except TimeoutError:
                self.last_error = "OpenAI timeout"
                if attempt >= self.max_retries:
                    return None
            except urllib.error.URLError as exc:
                self.last_error = f"OpenAI URL error: {exc.reason}"
                if attempt >= self.max_retries:
                    return None
            except json.JSONDecodeError:
                self.last_error = "OpenAI JSON invalido"
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
            self.last_error = "OpenAI devolvio JSON invalido"
            return None
        if not isinstance(parsed, dict):
            self.last_error = "OpenAI devolvio formato inesperado"
            return None
        return parsed


def _extract_output_text(decoded: dict) -> str | None:
    texts = []
    for item in decoded.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                texts.append(content.get("text", ""))
    text = "".join(texts).strip()
    return text or None


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_term(term: str) -> str:
    return _TERM_RE.sub(" ", term.strip().lower())
