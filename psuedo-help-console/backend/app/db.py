"""SQLite database setup and helpers."""
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

DB_PATH = Path("demo.db")


def get_connection() -> sqlite3.Connection:
    """Get SQLite connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            repo TEXT,
            pr_number INTEGER,
            pr_title TEXT,
            author TEXT,
            head_sha TEXT,
            mode TEXT,
            status TEXT,
            gate_outcome TEXT,
            gate_reasons_json TEXT,
            created_at TEXT,
            finished_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS steps (
            id TEXT PRIMARY KEY,
            run_id TEXT,
            name TEXT,
            status TEXT,
            message TEXT,
            logs TEXT,
            started_at TEXT,
            finished_at TEXT,
            FOREIGN KEY (run_id) REFERENCES runs(id)
        )
    """)

    # Ensure logs column exists if table was created previously
    try:
        c.execute("ALTER TABLE steps ADD COLUMN logs TEXT")
    except Exception:
        pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS findings (
            id TEXT PRIMARY KEY,
            run_id TEXT,
            severity TEXT,
            file TEXT,
            line INTEGER,
            title TEXT,
            explanation TEXT,
            suggested_fix TEXT,
            confidence REAL,
            FOREIGN KEY (run_id) REFERENCES runs(id)
        )
    """)

    conn.commit()
    conn.close()


def reset_db() -> None:
    """Drop all data for demo reset."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM findings")
    c.execute("DELETE FROM steps")
    c.execute("DELETE FROM runs")
    conn.commit()
    conn.close()


def get_run_with_details(run_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a run along with its ordered steps and findings."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
    run_row = c.fetchone()
    if not run_row:
        conn.close()
        return None

    run = dict(run_row)
    if run.get("gate_reasons_json"):
        try:
            run["gate_reasons"] = json.loads(run["gate_reasons_json"])
        except Exception:
            run["gate_reasons"] = []
    else:
        run["gate_reasons"] = []

    c.execute("SELECT * FROM steps WHERE run_id = ? ORDER BY started_at ASC, id ASC", (run_id,))
    run["steps"] = [dict(row) for row in c.fetchall()]

    c.execute("SELECT * FROM findings WHERE run_id = ? ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END", (run_id,))
    run["findings"] = [dict(row) for row in c.fetchall()]

    conn.close()
    return run


def list_all_runs(limit: int = 50) -> List[Dict[str, Any]]:
    """List runs ordered by creation date descending."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM runs
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,))
    runs = [dict(row) for row in c.fetchall()]
    for r in runs:
        if r.get("gate_reasons_json"):
            try:
                r["gate_reasons"] = json.loads(r["gate_reasons_json"])
            except Exception:
                r["gate_reasons"] = []
        else:
            r["gate_reasons"] = []
    conn.close()
    return runs
