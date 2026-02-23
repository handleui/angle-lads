import json
import re
from pathlib import Path

_DICT_DIR = Path(__file__).parent / "dictionary"

# (compiled_pattern, canonical_term, info)
_patterns: list[tuple[re.Pattern, str, dict]] = []

# Conjugation suffixes for Spanglish -ear verbs (regular -ar pattern)
_EAR_SUFFIXES = [
    # infinitive
    "ear",
    # present indicative (yo / tú / vos / él / nosotros / ellos)
    "eo", "eas", "eás", "ea", "eamos", "ean",
    # preterite
    "eé", "easte", "eó", "earon",
    # imperfect
    "eaba", "eabas", "eábamos", "eaban",
    # gerund
    "eando",
    # past participle (masc/fem × sing/pl)
    "eado", "eada", "eados", "eadas",
    # present subjunctive
    "ee", "ees", "eemos", "een",
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

    alts = "|".join(
        re.escape(f) for f in sorted(all_forms, key=len, reverse=True)
    )
    return rf"\b(?:{alts})\b"


def _load():
    """Load all dictionary JSON files and build match patterns."""
    for path in _DICT_DIR.glob("*.json"):
        generation = path.stem
        with open(path) as f:
            entries = json.load(f)
        for term, definition in entries.items():
            info = {"definition": definition, "generation": generation}
            term_lower = term.lower()
            pattern = _build_pattern(term_lower)
            _patterns.append((re.compile(pattern), term_lower, info))


def scan(text: str) -> list[dict]:
    """Scan text for known slang terms.

    Returns a list of matches:
      [{"term": str, "definition": str, "generation": str, "start": int, "end": int}]
    """
    if not _patterns:
        _load()

    text_lower = text.lower()
    matches = []

    for regex, term, info in _patterns:
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
