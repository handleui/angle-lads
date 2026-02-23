import json
import re
from pathlib import Path

_DICT_DIR = Path(__file__).parent / "dictionary"

# {term_lower: {"definition": str, "generation": str}}
_lookup: dict[str, dict] = {}


def _load():
    """Load all dictionary JSON files into the lookup table."""
    generation_map = {
        "gen_z": "gen_z",
        "millennial": "millennial",
        "boomer": "boomer",
        "regional": "regional",
    }
    for path in _DICT_DIR.glob("*.json"):
        generation = generation_map.get(path.stem, path.stem)
        with open(path) as f:
            entries = json.load(f)
        for term, definition in entries.items():
            _lookup[term.lower()] = {
                "definition": definition,
                "generation": generation,
            }


def scan(text: str) -> list[dict]:
    """Scan text for known slang terms.

    Returns a list of matches:
      [{"term": str, "definition": str, "generation": str, "start": int, "end": int}]
    """
    if not _lookup:
        _load()

    text_lower = text.lower()
    matches = []

    for term, info in _lookup.items():
        for m in re.finditer(r"\b" + re.escape(term) + r"\b", text_lower):
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
