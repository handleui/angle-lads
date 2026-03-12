import json
import re
import threading
from pathlib import Path

_DICT_DIR = Path(__file__).parent / "dictionary"
_WORD_RE = re.compile(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ][0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ'_-]*")
_LOAD_LOCK = threading.Lock()
_GENERATION_ALIASES = {
    "regional": "boomer",
}

_exact_phrase_map: dict[str, dict] = {}
_regex_patterns: list[tuple[re.Pattern, str, dict]] = []
_terms: list[str] = []
_exact_terms: dict[str, dict] = {}
_generation_terms: dict[str, list[str]] = {}
_max_term_words = 1
_REFERENCE_GENERATIONS = ("boomer", "millennial", "gen_z")

# Conjugation suffixes for Spanglish -ear verbs (regular -ar pattern)
_EAR_SUFFIXES = [
    # infinitive
    "ear",
    # present indicative (yo / tú / vos / él / nosotros / ellos)
    "eo",
    "eas",
    "eás",
    "ea",
    "eamos",
    "ean",
    # preterite
    "eé",
    "easte",
    "eó",
    "earon",
    # imperfect
    "eaba",
    "eabas",
    "eábamos",
    "eaban",
    # gerund
    "eando",
    # past participle (masc/fem × sing/pl)
    "eado",
    "eada",
    "eados",
    "eadas",
    # present subjunctive
    "ee",
    "ees",
    "eemos",
    "een",
]


def _phonetic_variants(term: str) -> list[str]:
    """Generate likely Deepgram mistranscription variants for Spanglish terms.

    Covers the most common Spanish phonology mismatches:
    - Prosthetic 'e' before s+stop clusters (stalkear → estalkear)
    - 'gh' simplification (ghostear → gostear / jostear)
    - Double consonant reduction (shippear → shipear)
    - 'sh' → 'ch' at word start (shipear → chipear)
    """
    variants: set[str] = set()

    # Prosthetic 'e' before s + stop/fricative (very common in Spanish)
    if re.match(r"^s[tpck]", term):
        variants.add("e" + term)

    # 'gh' → 'g' or 'j'
    if "gh" in term:
        variants.add(term.replace("gh", "g"))
        variants.add(term.replace("gh", "j"))

    # Double consonant → single
    simplified = re.sub(r"([bcdfghjklmnpqrstvwxyz])\1", r"\1", term)
    if simplified != term:
        variants.add(simplified)

    # 'sh' at word start → 'ch' (Spanish lacks /ʃ/, maps to /tʃ/)
    if term.startswith("sh"):
        variants.add("ch" + term[2:])
    if simplified != term and simplified.startswith("sh"):
        variants.add("ch" + simplified[2:])

    variants.discard(term)
    return list(variants)


def _build_pattern(term: str) -> str:
    """Build a regex pattern for a term, including phonetic variants and inflections."""
    all_forms = [term] + _phonetic_variants(term)

    if term.endswith("ear") and len(term) > 3:
        # Verb: combine stems from all phonetic variants, share suffix alternation
        stems = sorted(
            {re.escape(f[:-3]) for f in all_forms if f.endswith("ear")},
            key=len,
            reverse=True,
        )
        stems_alt = "|".join(stems)
        suffixes_alt = "|".join(re.escape(s) for s in _EAR_SUFFIXES)
        return rf"\b(?:{stems_alt})(?:{suffixes_alt})\b"

    if term.endswith("o") and len(term) > 4:
        # Spanish adjective/noun: match -o / -a / -os / -as
        stem = re.escape(term[:-1])
        return rf"\b{stem}[oa]s?\b"

    if len(all_forms) == 1:
        return r"\b" + re.escape(term) + r"\b"

    alts = "|".join(re.escape(f) for f in sorted(all_forms, key=len, reverse=True))
    return rf"\b(?:{alts})\b"


def _requires_regex(term: str) -> bool:
    if term.endswith("ear") and len(term) > 3:
        return True
    if term.endswith("o") and len(term) > 4:
        return True
    return bool(_phonetic_variants(term))


def _load():
    """Load all dictionary JSON files and build match patterns."""
    global _max_term_words
    for path in _DICT_DIR.glob("*.json"):
        generation = _GENERATION_ALIASES.get(path.stem, path.stem)
        _generation_terms.setdefault(generation, [])
        with open(path) as f:
            entries = json.load(f)
        for term, definition in entries.items():
            info = {"definition": definition, "generation": generation}
            term_lower = term.lower()
            _terms.append(term)
            _exact_terms[term_lower] = info
            _generation_terms[generation].append(term)
            _max_term_words = max(_max_term_words, len(term_lower.split()))
            if _requires_regex(term_lower):
                pattern = _build_pattern(term_lower)
                _regex_patterns.append((re.compile(pattern), term_lower, info))
            else:
                _exact_phrase_map[term_lower] = info


def _ensure_loaded():
    if _terms:
        return
    with _LOAD_LOCK:
        if not _terms:
            _load()


def scan(text: str) -> list[dict]:
    """Scan text for known slang terms.

    Returns a list of matches:
      [{"term": str, "definition": str, "generation": str, "start": int, "end": int}]
    """
    _ensure_loaded()

    text_lower = text.lower()
    matches = _scan_exact_terms(text_lower)
    for regex, term, info in _regex_patterns:
        for m in regex.finditer(text_lower):
            matches.append(
                {
                    "term": term,
                    "definition": info["definition"],
                    "generation": info["generation"],
                    "start": m.start(),
                    "end": m.end(),
                }
            )

    matches.sort(key=lambda x: x["start"])
    return matches


def transcription_prompt(max_terms: int = 80) -> str:
    _ensure_loaded()
    unique_terms = []
    seen = set()
    for term in _terms:
        normalized = term.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_terms.append(term)
    return ", ".join(unique_terms[:max_terms])


def lookup_term(term: str) -> dict | None:
    _ensure_loaded()
    normalized = term.strip().lower()
    if not normalized:
        return None
    info = _exact_terms.get(normalized)
    if info is None:
        return None
    return {
        "term": normalized,
        "definition": info["definition"],
        "generation": info["generation"],
    }


def generation_reference() -> dict[str, list[str]]:
    _ensure_loaded()
    return {
        generation: list(_generation_terms.get(generation, []))
        for generation in _REFERENCE_GENERATIONS
    }


def _scan_exact_terms(text_lower: str) -> list[dict]:
    if not _exact_phrase_map:
        return []

    tokens = list(_WORD_RE.finditer(text_lower))
    if not tokens:
        return []

    matches = []
    for size in range(1, min(_max_term_words, len(tokens)) + 1):
        for start in range(len(tokens) - size + 1):
            phrase = " ".join(token.group(0) for token in tokens[start : start + size])
            info = _exact_phrase_map.get(phrase)
            if info is None:
                continue
            matches.append(
                {
                    "term": phrase,
                    "definition": info["definition"],
                    "generation": info["generation"],
                    "start": tokens[start].start(),
                    "end": tokens[start + size - 1].end(),
                }
            )
    return matches
