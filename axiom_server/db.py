"""SQLite storage for run metadata."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_DEFAULT_DB = Path("data/axiom.db")

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    verdict       TEXT NOT NULL,
    failed_gate   TEXT,
    top_fix       TEXT,
    evidence_path TEXT NOT NULL
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


class RunStore:
    """Thin wrapper around an SQLite database for run records."""

    def __init__(self, db_path: Path = _DEFAULT_DB) -> None:
        self._conn = _connect(db_path)

    def insert(
        self,
        *,
        run_id: str,
        task_id: str,
        created_at: str,
        verdict: str,
        failed_gate: str | None,
        top_fix: str | None,
        evidence_path: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO runs (run_id, task_id, created_at, verdict, failed_gate, top_fix, evidence_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, task_id, created_at, verdict, failed_gate, top_fix, evidence_path),
        )
        self._conn.commit()

    def get(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_recent(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
