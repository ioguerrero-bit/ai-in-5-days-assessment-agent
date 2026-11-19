"""Persistent session & vector-indexed evaluation store backed by SQLite.

Stores learner assessment reports, historical score deltas, and long-term
consolidated memories with deterministic feature-hashing vector embeddings for
semantic retrieval across turns and resubmissions. All records pass through
PII redaction prior to persistence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from assessment_agent.observability.pii_redactor import redact_pii

_VECTOR_DIMS = 64
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}")


def compute_text_embedding(text: str, dims: int = _VECTOR_DIMS) -> list[float]:
    """Compute a normalized deterministic hashing embedding for lightweight local vector search."""
    vec = [0.0] * dims
    tokens = _TOKEN_RE.findall(text.lower())
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if (digest[4] % 2 == 0) else -1.0
        vec[bucket] += sign

    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [round(v / norm, 6) for v in vec]


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Return cosine similarity between two equal-length embedding vectors."""
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class PersistentEvaluationStore:
    """SQLite-backed persistent store for session history, rubric runs, and vector memories."""

    def __init__(self, db_path: str = "./data/assessment_history.db") -> None:
        self._db_path = Path(db_path)
        if str(db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS assessment_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    learner_id TEXT NOT NULL,
                    repo_url TEXT NOT NULL,
                    report_language TEXT NOT NULL,
                    total_score INTEGER NOT NULL,
                    hitl_required INTEGER NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learner_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    learner_id TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    memory_text TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    updated_at TEXT NOT NULL,
                    UNIQUE(learner_id, memory_key)
                )
                """
            )
            conn.commit()

    def save_assessment_run(
        self,
        *,
        learner_id: str,
        repo_url: str,
        report_language: str,
        total_score: int,
        hitl_required: bool,
        report_payload: dict[str, Any],
    ) -> int:
        """Persist a PII-scrubbed evaluation run and return its record ID."""
        scrubbed_learner = redact_pii(learner_id)
        scrubbed_repo = redact_pii(repo_url)
        scrubbed_payload = redact_pii(report_payload)
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO assessment_runs (
                    learner_id, repo_url, report_language, total_score,
                    hitl_required, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scrubbed_learner,
                    scrubbed_repo,
                    report_language,
                    total_score,
                    1 if hitl_required else 0,
                    json.dumps(scrubbed_payload, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)

    def get_recent_runs(self, learner_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve a learner's most recent assessment runs for progress tracking."""
        scrubbed_learner = redact_pii(learner_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, learner_id, repo_url, report_language, total_score,
                       hitl_required, report_json, created_at
                FROM assessment_runs
                WHERE learner_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (scrubbed_learner, limit),
            ).fetchall()

        return [
            {
                "id": row["id"],
                "learner_id": row["learner_id"],
                "repo_url": row["repo_url"],
                "report_language": row["report_language"],
                "total_score": row["total_score"],
                "hitl_required": bool(row["hitl_required"]),
                "report": json.loads(row["report_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def upsert_memory(
        self,
        *,
        learner_id: str,
        memory_key: str,
        memory_text: str,
        confidence: float = 1.0,
    ) -> None:
        """Upsert a deduplicated, PII-scrubbed long-term memory with vector embedding."""
        scrubbed_learner = redact_pii(learner_id)
        scrubbed_text = redact_pii(memory_text)
        embedding = compute_text_embedding(scrubbed_text)
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO learner_memories (
                    learner_id, memory_key, memory_text, embedding_json, confidence, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(learner_id, memory_key) DO UPDATE SET
                    memory_text = excluded.memory_text,
                    embedding_json = excluded.embedding_json,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (
                    scrubbed_learner,
                    memory_key,
                    scrubbed_text,
                    json.dumps(embedding),
                    confidence,
                    now,
                ),
            )
            conn.commit()

    def search_memories(
        self,
        learner_id: str,
        query: str,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """Retrieve the most semantically relevant consolidated memories for a learner."""
        scrubbed_learner = redact_pii(learner_id)
        query_vec = compute_text_embedding(redact_pii(query))

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT memory_key, memory_text, embedding_json, confidence, updated_at
                FROM learner_memories
                WHERE learner_id = ?
                """,
                (scrubbed_learner,),
            ).fetchall()

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            stored_vec = json.loads(row["embedding_json"])
            sim = cosine_similarity(query_vec, stored_vec)
            scored.append(
                (
                    sim,
                    {
                        "memory_key": row["memory_key"],
                        "memory_text": row["memory_text"],
                        "similarity": round(sim, 4),
                        "confidence": row["confidence"],
                        "updated_at": row["updated_at"],
                    },
                )
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]
