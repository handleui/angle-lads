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
        "min_confidence": 0.8,
        "cooldown_seconds": 50,
        "min_request_seconds": 2.4,
        "context_lines": 3,
    },
    "privado": {
        "min_confidence": 0.83,
        "cooldown_seconds": 36,
        "min_request_seconds": 1.5,
        "context_lines": 5,
    },
    "focus": {
        "min_confidence": 0.77,
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
_KNOWN_TERMS_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "term",
                    "why_in_context",
                    "usage_mode",
                    "confidence",
                ],
                "properties": {
                    "term": {"type": "string"},
                    "why_in_context": {"type": "string"},
                    "usage_mode": {
                        "type": "string",
                        "enum": ["colloquial", "literal", "unclear", "none"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        }
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
        self.min_confidence = max(
            config.OPENAI_EXPLANATION_CONFIDENCE_THRESHOLD,
            selected["min_confidence"],
        )
        self.cooldown_seconds = selected["cooldown_seconds"]
        self.min_request_seconds = selected["min_request_seconds"]
        self.context_lines = selected["context_lines"]

    def explain(self, latest_line: str, history: list[str]) -> dict | None:
        explanations = self.explain_many(latest_line, history)
        return explanations[0] if explanations else None

    def explain_many(self, latest_line: str, history: list[str]) -> list[dict]:
        if not self.enabled:
            return self._explain_many_from_dictionary(latest_line)

        self.last_error = None
        dictionary_matches = [
            match
            for match in self._pick_dictionary_matches(latest_line)
            if self._passes_cooldown(match["term"])
        ]
        if dictionary_matches:
            if not self._wait_for_request_slot():
                return [
                    self._build_dictionary_explanation(match, _DICTIONARY_WHY)
                    for match in dictionary_matches
                ]
            explanations = self._explain_known_terms(
                dictionary_matches,
                latest_line,
                history,
            )
            if explanations is None:
                return [
                    self._build_dictionary_explanation(match, _DICTIONARY_WHY)
                    for match in dictionary_matches
                ]
            return explanations

        if not self._wait_for_request_slot():
            return []

        text = self._generate_json(latest_line, history)
        if text is None:
            return []

        parsed = self._parse_json(text)
        if parsed is None or not parsed.get("should_flag"):
            return []

        term = str(parsed.get("term", "")).strip()
        definition = str(parsed.get("definition", "")).strip()
        why = str(parsed.get("why_in_context", "")).strip()
        term_kind = str(parsed.get("term_kind", "none")).strip()
        usage_mode = str(parsed.get("usage_mode", "none")).strip()
        target_generation = str(parsed.get("target_generation", "unknown")).strip()
        confidence = _to_float(parsed.get("confidence"))

        if not term or not definition or not why:
            return []
        if confidence < self.min_confidence:
            return []
        if term_kind in {"none", "nickname", "proper_noun", "unclear"}:
            return []
        if usage_mode in {"none", "unclear"}:
            return []
        if target_generation == "unknown":
            return []
        if not self._is_valid_term(term, latest_line):
            return []
        if not self._passes_cooldown(term):
            return []

        dictionary_entry = detector.lookup_term(term)
        if dictionary_entry is not None:
            explanation = self._build_dictionary_explanation(dictionary_entry, why)
        else:
            if not self._passes_span_sanity(term, term_kind):
                return []
            explanation = {
                "term": term,
                "definition": definition,
                "why_in_context": why,
                "target_generation": target_generation,
                "confidence": confidence,
                "model": self.model,
                "source": "ai",
            }
        return [explanation]

    def _explain_from_dictionary(self, latest_line: str) -> dict | None:
        explanations = self._explain_many_from_dictionary(latest_line)
        return explanations[0] if explanations else None

    def _explain_many_from_dictionary(self, latest_line: str) -> list[dict]:
        matches = self._pick_dictionary_matches(latest_line)
        if matches:
            return [
                self._build_dictionary_explanation(match, _DICTIONARY_WHY)
                for match in matches
            ]
        cached = self._cache.lookup(latest_line)
        return [cached] if cached is not None else []

    def _pick_dictionary_matches(self, latest_line: str) -> list[dict]:
        matches = [
            match
            for match in detector.scan(latest_line)
            if is_cacheable_term(match.get("term", ""))
        ]
        if not matches:
            return []
        matches.sort(
            key=lambda match: (
                match["start"],
                -(match["end"] - match["start"]),
            )
        )
        selected = []
        last_end = -1
        seen_terms = set()
        for match in matches:
            term = _normalize_term(match["term"])
            if match["start"] < last_end or term in seen_terms:
                continue
            selected.append(match)
            seen_terms.add(term)
            last_end = match["end"]
        return selected[:2]

    def _build_dictionary_explanation(self, match: dict, why: str) -> dict:
        return {
            "term": match["term"],
            "definition": match["definition"],
            "why_in_context": why,
            "target_generation": _fallback_generation(match["generation"]),
            "confidence": 1.0,
            "model": "dictionary",
            "source": "dictionary",
        }

    def _explain_known_terms(
        self,
        matches: list[dict],
        latest_line: str,
        history: list[str],
    ) -> list[dict] | None:
        text = self._generate_known_terms_json(matches, latest_line, history)
        if text is None:
            return None
        parsed = self._parse_json(text)
        if parsed is None:
            return None
        items = parsed.get("items")
        if not isinstance(items, list):
            return None

        by_term = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_term(str(item.get("term", "")))
            if not normalized:
                continue
            by_term[normalized] = item

        explanations = []
        for match in matches:
            item = by_term.get(_normalize_term(match["term"]))
            if not isinstance(item, dict):
                continue
            why = str(item.get("why_in_context", "")).strip()
            usage_mode = str(item.get("usage_mode", "none")).strip()
            confidence = _to_float(item.get("confidence"))
            if (
                not why
                or usage_mode in {"none", "unclear"}
                or confidence < self.min_confidence
            ):
                continue
            if usage_mode == "literal" and confidence < (self.min_confidence + 0.08):
                continue
            explanations.append(self._build_dictionary_explanation(match, why))
        return explanations

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

    def _passes_span_sanity(self, term: str, term_kind: str) -> bool:
        normalized = _normalize_term(term)
        words = normalized.split()
        if not words:
            return False
        if len(words) > 2:
            return False
        if any(len(word) < 2 for word in words):
            return False
        if len(words) == 2 and min(len(word) for word in words) < 4:
            return False
        if len(words) == 1 and len(words[0]) < 3:
            return False
        if not any(len(word) >= 4 for word in words):
            return False
        if term_kind == "common_word" and len(words) == 2:
            return False
        if any(not re.fullmatch(r"[0-9a-záéíóúüñ'_-]+", word) for word in words):
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
            "- frase: Ese feature flag nos está frenando el release.\n"
            "  salida: {should_flag:true, term:'feature flag', "
            "term_kind:technical_term, usage_mode:literal}\n"
            "- frase: Ese güey ya me ghosteó.\n"
            "  salida: {should_flag:true, term:'ghosteó', "
            "term_kind:spanglish, usage_mode:colloquial}\n"
            "- frase: Qué cringe me dio.\n"
            "  salida: {should_flag:true, term:'cringe', "
            "term_kind:slang, usage_mode:colloquial}\n"
            "- frase: Lo dijo muy en modo passive aggressive.\n"
            "  salida: {should_flag:true, term:'passive aggressive', "
            "term_kind:common_word, usage_mode:colloquial}\n"
            "- frase: La mamá de Luis llegó.\n"
            "  salida: {should_flag:false, term_kind:none, usage_mode:literal}\n"
            "- frase: Mom, pásame eso.\n"
            "  salida: {should_flag:false, term_kind:none, usage_mode:literal}\n"
        )
        instructions = (
            "Eres un interprete sociolinguistico de espanol mexicano y spanglish. "
            "Debes detectar si la ultima frase contiene un termino o expresion breve "
            "que podria confundir a un oyente general en este contexto. "
            "La mayoria de las frases NO necesitan explicacion. "
            "Evalua SOLO la conversacion recibida. "
            "No confundas instrucciones del sistema con texto de la conversacion. "
            "Asume que la conversacion o monologo es principalmente "
            "intra-generacional. "
            "No busques solo slang intergeneracional. "
            "Marca should_flag=true cuando una palabra o expresion breve sea "
            "confusa por ser coloquial, spanglish, regional, demasiado contextual, "
            "muy internetera, metaforica o tecnica para una persona general. "
            "Si no existe un termino realmente confuso o relevante, responde "
            "should_flag=false y term_kind=none. "
            "Solo puedes marcar un termino si aparece literalmente o casi "
            "literalmente en la ultima frase. "
            "Marca una sola palabra o expresion breve, no una frase completa. "
            "Evita fragmentos funcionales o sintacticos que suenan incompletos, "
            "por ejemplo conectores, coletillas, pedazos de oracion o combinaciones "
            "sin sentido por si solas. "
            "No marques secuencias como 'haciendo de que', 'de que', 'como que', "
            "'pero no', ni verbos comunes aislados solo porque el audio los deformo. "
            "Usa la referencia como ejemplos utiles, no como una lista que "
            "debas forzar. "
            "Debes elegir target_generation como una de estas tres: "
            "boomer, millennial o gen_z. "
            "Usa estas anclas rapidas para target_generation: "
            "boomer para expresiones mas viejas, regionales, old-school o "
            "claramente previas a internet; "
            "millennial para slang e internet masivo de 2000s y 2010s, tono "
            "casual ampliamente difundido y spanglish ya bastante normalizado; "
            "gen_z para slang mas nuevo, muy de redes, de gaming, fandom, "
            "parasocial o spanglish verbal mas reciente. "
            "No mandes palabras muy modernas a boomer salvo que la referencia "
            "local ya las marque asi. "
            "No mandes todo lo moderno a millennial por defecto. "
            "Elige target_generation segun quien probablemente necesitaria mas "
            "contexto para entender ese termino, aunque la charla sea de la misma "
            "generacion. "
            "Si el termino aparece en la referencia local, respeta esa "
            "generacion. "
            "Prioriza terminos cuyo sentido depende de esta conversacion y de esa "
            "frase, no solo de un diccionario. "
            "No marques nombres propios, apodos de personas, siglas, marcas, "
            "palabras de interfaz, ni jerga laboral interna demasiado local. "
            "Si una palabra puede entenderse como uso literal o sustantivo "
            "comun sin friccion real, responde should_flag=false. "
            "Si la frase ya es comprensible para un hablante general de "
            "espanol mexicano, responde should_flag=false. "
            "No conviertas palabras familiares basicas como madre, mama, mom, "
            "papa, cable, linea o nombres de personas en slang. "
            "why_in_context debe explicar por que se uso ese termino en esta "
            "conversacion y en esa frase, no solo definir la palabra. "
            "definition debe aclarar el sentido util en este contexto, no una "
            "definicion academica amplia. "
            "Si sirve, menciona el tema o la situacion de la charla de forma breve. "
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

    def _generate_known_terms_json(
        self,
        matches: list[dict],
        latest_line: str,
        history: list[str],
    ) -> str | None:
        history_text = "\n".join(f"- {line}" for line in history[-self.context_lines :])
        fixed_terms = "\n".join(
            (
                f"- term: {match['term']}\n"
                f"  definition: {match['definition']}\n"
                f"  generation: {_fallback_generation(match['generation'])}"
            )
            for match in matches
        )
        instructions = (
            "Eres un interprete sociolinguistico de espanol mexicano y spanglish. "
            "Ya se detectaron uno o varios terminos validos del diccionario local. "
            "NO decidas si deben marcarse o no: ya deben marcarse. "
            "Devuelve un item por cada termino dado y no inventes terminos nuevos. "
            "Tu tarea es explicar por que se uso cada termino en esta "
            "conversacion y en esta frase. "
            "No cambies los terminos, no cambies las generaciones, no redefinas las "
            "palabras fuera de este contexto. "
            "why_in_context debe ser breve, claro y situacional para cada termino. "
            "Si ayuda, menciona el tono, tema o intencion de la frase. "
            "usage_mode debe ser colloquial o literal segun el uso en la frase. "
            "Usa espanol claro y breve."
        )
        prompt = (
            f"Terminos fijados:\n{fixed_terms}\n\n"
            f"Conversacion reciente:\n{history_text}\n\n"
            f"Ultima frase:\n{latest_line}\n"
        )
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": prompt,
            "store": False,
            "temperature": 0,
            "max_output_tokens": 120,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "known_terms_context_explanation",
                    "strict": True,
                    "schema": _KNOWN_TERMS_OUTPUT_SCHEMA,
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
