import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path

_DB_PATH = Path(__file__).parent / ".angle-lads-cache.sqlite3"
_WORD_RE = re.compile(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ][0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ'_-]*")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.lower().split())


class TermCache:
    def __init__(self, path: Path = _DB_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._ensure_schema()

    def lookup(self, text: str) -> dict | None:
        normalized_text = _normalize(text)
        if not normalized_text:
            return None

        with self._lock, sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                """
                SELECT term, term_normalized, definition, why_in_context, target_generation, confidence
                FROM term_cache
                ORDER BY LENGTH(term_normalized) DESC, updated_at DESC
                """
            ).fetchall()

        for row in rows:
            term, term_normalized, definition, why_in_context, target_generation, confidence = row
            if not term_normalized:
                continue
            pattern = rf"(?<!\w){re.escape(term_normalized)}(?!\w)"
            if not re.search(pattern, normalized_text):
                continue
            return {
                "term": term,
                "definition": definition,
                "why_in_context": why_in_context,
                "target_generation": target_generation,
                "confidence": float(confidence),
                "model": "cache",
            }
        return None

    def store(self, explanation: dict):
        term = str(explanation.get("term", "")).strip()
        if not term:
            return

        with self._lock, sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO term_cache (
                    term,
                    term_normalized,
                    definition,
                    why_in_context,
                    target_generation,
                    confidence,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(term_normalized) DO UPDATE SET
                    term=excluded.term,
                    definition=excluded.definition,
                    why_in_context=excluded.why_in_context,
                    target_generation=excluded.target_generation,
                    confidence=excluded.confidence,
                    updated_at=excluded.updated_at
                """,
                (
                    term,
                    _normalize(term),
                    str(explanation.get("definition", "")).strip(),
                    str(explanation.get("why_in_context", "")).strip(),
                    str(explanation.get("target_generation", "unknown")).strip(),
                    float(explanation.get("confidence", 0)),
                    int(time.time()),
                ),
            )
            conn.commit()

    def _ensure_schema(self):
        with self._lock, sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS term_cache (
                    term TEXT NOT NULL,
                    term_normalized TEXT PRIMARY KEY,
                    definition TEXT NOT NULL,
                    why_in_context TEXT NOT NULL,
                    target_generation TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.commit()
