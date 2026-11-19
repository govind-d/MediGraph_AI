"""SQLite connection handling and schema bootstrap.

The layer is intentionally thin so the same SQL works against PostgreSQL after
swapping the driver (see notes in schema.sql).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from utils.config import DATA_DIR, Config
from utils.helpers import now_iso
from utils.security import log_error, log_info

_INITIALISED = False


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or Config.DB_PATH
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def query(sql: str, params=(), *, db_path: str | None = None):
    with get_connection(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params=(), *, db_path: str | None = None):
    rows = query(sql, params, db_path=db_path)
    return rows[0] if rows else None


def execute(sql: str, params=(), *, db_path: str | None = None) -> int:
    """Run an INSERT/UPDATE/DELETE and return lastrowid (or rowcount)."""
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid or cur.rowcount


def executemany(sql: str, seq, *, db_path: str | None = None) -> int:
    with get_connection(db_path) as conn:
        cur = conn.executemany(sql, list(seq))
        conn.commit()
        return cur.rowcount


# Columns added after the first release, applied to existing databases on startup.
_MIGRATIONS = [
    ("prescriptions", "source", "TEXT DEFAULT 'typed'"),
    ("prescriptions", "ocr_engine", "TEXT"),
    ("prescriptions", "ocr_confidence", "REAL"),
    ("prescriptions", "ocr_verified", "INTEGER DEFAULT 0"),
]


def _migrate(db_path: str | None = None) -> list:
    """Add any columns missing from an older database file."""
    applied = []
    with get_connection(db_path) as conn:
        for table, column, definition in _MIGRATIONS:
            try:
                existing = {row["name"] for row in
                            conn.execute("PRAGMA table_info(%s)" % table).fetchall()}
            except Exception:  # pragma: no cover - table absent
                continue
            if not existing or column in existing:
                continue
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, definition))
            applied.append("%s.%s" % (table, column))
        conn.commit()
    if applied:
        log_info("Applied database migrations: %s" % ", ".join(applied))
    return applied


def init_db(db_path: str | None = None, *, seed: bool = True) -> None:
    """Create tables (idempotent), migrate, and seed the knowledge base."""
    global _INITIALISED
    schema = Path(Config.SCHEMA_PATH).read_text(encoding="utf-8")
    with get_connection(db_path) as conn:
        conn.executescript(schema)
        conn.commit()
    _migrate(db_path)
    if seed:
        seed_interactions(db_path)
        seed_medications(db_path)
    _INITIALISED = True


def ensure_db() -> None:
    if not _INITIALISED:
        init_db()


def _load_json(filename: str):
    path = DATA_DIR / filename
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:  # pragma: no cover - defensive
        log_error("loading %s" % filename, exc)
        return []


def seed_interactions(db_path: str | None = None) -> int:
    rows = _load_json("sample_interactions.json")
    if not rows:
        return 0
    payload = [
        (
            str(r.get("drug_a", "")).lower(),
            str(r.get("drug_b", "")).lower(),
            str(r.get("severity", "informational")).lower(),
            r.get("mechanism"),
            r.get("description"),
            r.get("management"),
            r.get("source_name"),
            r.get("source_url"),
        )
        for r in rows
    ]
    count = executemany(
        """INSERT OR REPLACE INTO drug_interactions
           (drug_a, drug_b, severity, mechanism, description, management, source_name, source_url)
           VALUES (?,?,?,?,?,?,?,?)""",
        payload,
        db_path=db_path,
    )
    log_info("Seeded %d drug interaction records" % count)
    return count


def seed_medications(db_path: str | None = None) -> int:
    rows = _load_json("sample_medications.json")
    if not rows:
        return 0
    ts = now_iso()
    payload = [
        (
            str(r.get("normalized_name", "")).lower(),
            r.get("display_name") or r.get("normalized_name"),
            r.get("rxcui"),
            r.get("drug_class"),
            json.dumps(r.get("ingredients", [])),
            r.get("common_uses"),
            r.get("source", "Local RxNorm-derived dataset"),
            ts,
        )
        for r in rows
    ]
    count = executemany(
        """INSERT OR IGNORE INTO medications
           (normalized_name, display_name, rxcui, drug_class, ingredients, common_uses, source, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        payload,
        db_path=db_path,
    )
    log_info("Seeded %d medication records" % count)
    return count


def count_rows_safe(db_path: str | None = None) -> dict:
    """Row counts for the Settings page (never raises)."""
    tables = ["users", "patient_profiles", "prescriptions", "prescription_medications",
              "medications", "drug_interactions", "safety_reports", "medication_schedule",
              "adherence_records", "chat_history"]
    counts = {}
    for table in tables:
        try:
            row = query_one("SELECT COUNT(*) AS n FROM %s" % table, db_path=db_path)
            counts[table] = row["n"] if row else 0
        except Exception:
            counts[table] = "n/a"
    return counts


def reset_db(db_path: str | None = None) -> None:
    """Drop every table and rebuild. Used by tests and the Settings page."""
    tables = [
        "adherence_records",
        "medication_schedule",
        "chat_history",
        "safety_reports",
        "prescription_medications",
        "prescriptions",
        "medical_conditions",
        "patient_allergies",
        "patient_profiles",
        "users",
        "medications",
        "drug_interactions",
    ]
    with get_connection(db_path) as conn:
        for table in tables:
            conn.execute("DROP TABLE IF EXISTS %s" % table)
        conn.commit()
    init_db(db_path)
