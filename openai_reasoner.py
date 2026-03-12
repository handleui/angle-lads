import json
import random
import re
import threading
import time
import urllib.error
import urllib.request

import config
import detector
from term_cache import TermCache, is_cacheable_term

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_TERM_RE = re.compile(r"\s+")
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_DICTIONARY_WHY = "Se usa aqui como termino coloquial dentro de la conversacion."
_PRESETS = {
    "cafe": {
        "min_confidence": 0.88,
        "cooldown_seconds": 50,
        "min_request_seconds": 2.4,
        "context_lines": 3,
    },
    "privado": {
        "min_confidence": 0.9,
        "cooldown_seconds": 36,
        "min_request_seconds": 1.5,
        "context_lines": 5,
    },
    "focus": {
        "min_confidence": 0.86,
        "cooldown_seconds": 30,
        "min_request_seconds": 1.2,
        "context_lines": 6,
    },
}
_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "should_flag",
        "term_kind",
        "usage_mode",
        "term",
        "definition",
        "why_in_context",
        "target_generation",
        "confidence",
    ],
    "properties": {
        "should_flag": {"type": "boolean"},
        "term_kind": {
            "type": "string",
            "enum": [
                "slang",
                "spanglish",
                "regionalism",
                "nickname",
                "proper_noun",
                "common_word",
                "technical_term",
                "unclear",
                "none",
            ],
        },
        "usage_mode": {
            "type": "string",
            "enum": ["colloquial", "literal", "unclear", "none"],
        },
        "term": {"type": "string"},
        "definition": {"type": "string"},
        "why_in_context": {"type": "string"},
        "target_generation": {
            "type": "string",
            "enum": ["boomer", "millennial", "gen_z", "unknown"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


def max_context_lines() -> int:
    return max(
        config.OPENAI_EXPLANATION_CONTEXT_LINES,
        *(preset["context_lines"] for preset in _PRESETS.values()),
    )


class OpenAIContextReasoner:
    def __init__(self):
        self.api_key = config.OPENAI_API_KEY
        self.model = config.OPENAI_EXPLANATION_MODEL
        self.timeout_seconds = config.OPENAI_EXPLANATION_TIMEOUT_SECONDS
        self.min_confidence = config.OPENAI_EXPLANATION_CONFIDENCE_THRESHOLD
        self.cooldown_seconds = config.OPENAI_EXPLANATION_TERM_COOLDOWN_SECONDS
        self.min_request_seconds = max(
            0.0, config.OPENAI_EXPLANATION_MIN_REQUEST_SECONDS
        )
        self.max_retries = max(0, config.OPENAI_EXPLANATION_MAX_RETRIES)
        self.retry_base_seconds = max(
            0.05, config.OPENAI_EXPLANATION_RETRY_BASE_SECONDS
        )
        self.enabled = bool(self.api_key)
        self.last_error: str | None = None
        self._last_fired_at: dict[str, float] = {}
        self._cooldown_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._last_request_started_at = 0.0
        self._cache = TermCache()
        self.preset = "cafe"
        self.context_lines = config.OPENAI_EXPLANATION_CONTEXT_LINES
        self.set_preset(self.preset)

    def set_preset(self, preset: str):
        selected = _PRESETS.get(preset, _PRESETS["cafe"])
        self.preset = preset if preset in _PRESETS else "cafe"
        self.min_confidence = selected["min_confidence"]
        self.cooldown_seconds = selected["cooldown_seconds"]
        self.min_request_seconds = selected["min_request_seconds"]
        self.context_lines = selected["context_lines"]

    def explain(self, latest_line: str, history: list[str]) -> dict | None:
        if not self.enabled:
            return self._explain_from_dictionary(latest_line)

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
        term_kind = str(parsed.get("term_kind", "none")).strip()
        usage_mode = str(parsed.get("usage_mode", "none")).strip()
        target_generation = str(parsed.get("target_generation", "unknown")).strip()
        confidence = _to_float(parsed.get("confidence"))

        if not term or not definition or not why:
            return None
        if confidence < self.min_confidence:
            return None
        if term_kind not in {"slang", "spanglish", "regionalism"}:
            return None
        if usage_mode != "colloquial":
            return None
        if target_generation == "unknown":
            return None
        if not self._is_valid_term(term, latest_line):
            return None
        if not self._passes_cooldown(term):
            return None

        dictionary_entry = detector.lookup_term(term)
        if dictionary_entry is not None:
            explanation = {
                "term": dictionary_entry["term"],
                "definition": dictionary_entry["definition"],
                "why_in_context": _DICTIONARY_WHY,
                "target_generation": _fallback_generation(
                    dictionary_entry["generation"]
                ),
                "confidence": 1.0,
                "model": "dictionary",
                "source": "dictionary",
            }
        else:
            explanation = {
                "term": term,
                "definition": definition,
                "why_in_context": why,
                "target_generation": target_generation,
                "confidence": confidence,
                "model": self.model,
                "source": "ai",
            }
        return explanation

    def _explain_from_dictionary(self, latest_line: str) -> dict | None:
        dictionary_flags = detector.scan(latest_line)
        if dictionary_flags:
            match = dictionary_flags[0]
            return {
                "term": match["term"],
                "definition": match["definition"],
                "why_in_context": _DICTIONARY_WHY,
                "target_generation": _fallback_generation(match["generation"]),
                "confidence": 1.0,
                "model": "dictionary",
                "source": "dictionary",
            }
        return self._cache.lookup(latest_line)

    def _is_valid_term(self, term: str, latest_line: str) -> bool:
        normalized = _normalize_term(term)
        if not normalized:
            return False
        if not is_cacheable_term(term):
            return False
        latest_line_normalized = _normalize_term(latest_line)
        if normalized not in latest_line_normalized:
            return False
        return True

    def _wait_for_request_slot(self) -> bool:
        with self._request_lock:
            now = time.monotonic()
            wait_seconds = self.min_request_seconds - (
                now - self._last_request_started_at
            )
            if wait_seconds > 0:
                self.last_error = None
                time.sleep(wait_seconds)
                now = time.monotonic()
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
        history_text = "\n".join(f"- {line}" for line in history[-self.context_lines :])
        references = detector.generation_reference()
        reference_text = "\n".join(
            f"- {generation}: {', '.join(terms)}"
            for generation, terms in references.items()
            if terms
        )
        examples = (
            "- frase: Hola, madre.\n"
            "  salida: {should_flag:false, term_kind:none, usage_mode:literal}\n"
            "- frase: Pásame el cable.\n"
            "  salida: {should_flag:false, term_kind:none, usage_mode:literal}\n"
            "- frase: Ese güey ya me ghosteó.\n"
            "  salida: {should_flag:true, term:'ghosteó', "
            "term_kind:spanglish, usage_mode:colloquial}\n"
            "- frase: Qué cringe me dio.\n"
            "  salida: {should_flag:true, term:'cringe', "
            "term_kind:slang, usage_mode:colloquial}\n"
            "- frase: La mamá de Luis llegó.\n"
            "  salida: {should_flag:false, term_kind:none, usage_mode:literal}\n"
            "- frase: Mom, pásame eso.\n"
            "  salida: {should_flag:false, term_kind:none, usage_mode:literal}\n"
        )
        instructions = (
            "Eres un interprete sociolinguistico de espanol mexicano y spanglish. "
            "Debes detectar si la ultima frase contiene un termino que otra generacion "
            "podria no entender en este contexto. "
            "La mayoria de las frases NO necesitan explicacion. "
            "Evalua SOLO la conversacion recibida. "
            "No confundas instrucciones del sistema con texto de la conversacion. "
            "Primero decide si existe un termino claramente coloquial, "
            "spanglish o regional. "
            "Si no existe uno claro, responde should_flag=false y term_kind=none. "
            "Solo puedes marcar un termino si aparece literalmente o casi "
            "literalmente en la ultima frase. "
            "Marca una sola palabra o expresion breve, no una frase completa. "
            "Usa la referencia como ejemplos utiles, no como una lista que "
            "debas forzar. "
            "Debes elegir target_generation como una de estas tres: "
            "boomer, millennial o gen_z. "
            "Prioriza slang, spanglish y expresiones coloquiales reales ya "
            "conocidas en la referencia, pero puedes abstenerte. "
            "Si el termino no se parece a slang real, spanglish real o "
            "regionalismo claro, responde should_flag=false. "
            "No marques nombres propios, apodos de personas, siglas, marcas, "
            "palabras tecnicas, palabras de interfaz, "
            "jerga laboral interna ni sustantivos comunes solo porque suenen raros. "
            "Si una palabra puede entenderse como uso literal o sustantivo "
            "comun, responde should_flag=false. "
            "Si la frase ya es comprensible para un hablante general de "
            "espanol mexicano, responde should_flag=false. "
            "Si no hay termino coloquial claro, responde should_flag=false. "
            "No conviertas palabras familiares basicas como madre, mama, mom, "
            "papa, cable, linea o nombres de personas en slang. "
            "Usa espanol claro y breve en definition y why_in_context."
        )
        prompt = (
            f"Referencia por generacion:\n{reference_text}\n\n"
            f"Ejemplos:\n{examples}\n"
            f"Conversacion reciente:\n{history_text}\n\n"
            f"Ultima frase:\n{latest_line}\n"
        )
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": prompt,
            "store": False,
            "temperature": 0,
            "max_output_tokens": 160,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "colloquial_term_explanation",
                    "strict": True,
                    "schema": _OUTPUT_SCHEMA,
                }
            },
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
                if (
                    exc.code not in _RETRYABLE_STATUS_CODES
                    or attempt >= self.max_retries
                ):
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


def _fallback_generation(generation: str) -> str:
    if generation in {"boomer", "millennial", "gen_z"}:
        return generation
    return "millennial"
