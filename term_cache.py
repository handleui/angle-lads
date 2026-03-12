import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path

_DB_PATH = Path(__file__).parent / ".angle-lads-cache.sqlite3"
_CACHE_VERSION = 4
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
        candidates = _candidate_terms(normalized_text)
        if not candidates:
            return None
        placeholders = ", ".join("?" for _ in candidates)
        params = [
            *candidates,
            _CACHE_VERSION,
        ]

        with self._lock, sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    term,
                    term_normalized,
                    definition,
                    why_in_context,
                    target_generation,
                    confidence,
                    source,
                    version
                FROM term_cache
                WHERE term_normalized IN ({placeholders})
                  AND version = ?
                  AND source = 'dictionary'
                ORDER BY LENGTH(term_normalized) DESC, updated_at DESC
                """,
                params,
            ).fetchall()

        for row in rows:
            (
                term,
                term_normalized,
                definition,
                why_in_context,
                target_generation,
                confidence,
                source,
                version,
            ) = row
            if not term_normalized or not is_cacheable_term(term):
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
        source = str(explanation.get("source", "ai")).strip() or "ai"
        if source != "dictionary":
            return
        if not term or not is_cacheable_term(term):
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
                    source,
                    version,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(term_normalized) DO UPDATE SET
                    term=excluded.term,
                    definition=excluded.definition,
                    why_in_context=excluded.why_in_context,
                    target_generation=excluded.target_generation,
                    confidence=excluded.confidence,
                    source=excluded.source,
                    version=excluded.version,
                    updated_at=excluded.updated_at
                """,
                (
                    term,
                    _normalize(term),
                    str(explanation.get("definition", "")).strip(),
                    str(explanation.get("why_in_context", "")).strip(),
                    str(explanation.get("target_generation", "unknown")).strip(),
                    float(explanation.get("confidence", 0)),
                    source,
                    _CACHE_VERSION,
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
                    source TEXT NOT NULL DEFAULT 'ai',
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(term_cache)").fetchall()
            }
            if "source" not in columns:
                conn.execute(
                    "ALTER TABLE term_cache "
                    "ADD COLUMN source TEXT NOT NULL DEFAULT 'ai'"
                )
            if "version" not in columns:
                conn.execute(
                    "ALTER TABLE term_cache "
                    "ADD COLUMN version INTEGER NOT NULL DEFAULT 1"
                )
            conn.commit()


def is_cacheable_term(term: str) -> bool:
    normalized = _normalize(term)
    if not normalized:
        return False
    words = normalized.split()
    if len(words) > 3:
        return False
    if len(words) == 1 and len(words[0]) < 5:
        return False
    return True


def _candidate_terms(normalized_text: str) -> list[str]:
    words = [match.group(0) for match in _WORD_RE.finditer(normalized_text)]
    if not words:
        return []

    candidates: list[str] = []
    seen: set[str] = set()
    max_words = min(3, len(words))
    for size in range(max_words, 0, -1):
        for start in range(len(words) - size + 1):
            candidate = " ".join(words[start : start + size])
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)
    return candidates
