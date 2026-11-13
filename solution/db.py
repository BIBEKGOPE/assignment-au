"""SQLite connection setup and schema initialization for the data pipeline."""

import sqlite3
from pathlib import Path


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a configured SQLite connection for the pipeline."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the pipeline tables, indexes, and append-only event safeguards."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ingested_files (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('leads', 'events')),
            relpath TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            counters_json TEXT NOT NULL,
            ingested_at TEXT NOT NULL,
            UNIQUE (relpath, sha256)
        );

        -- attrs_json is only for non-PII optional fields; reports do not need customer_name.
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            external_ref TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            phone_hash TEXT NOT NULL,
            phone_last4 TEXT NOT NULL,
            attrs_json TEXT NOT NULL,
            UNIQUE (workflow_id, phone_hash)
        );

        CREATE TABLE IF NOT EXISTS lead_rejections (
            id INTEGER PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            reason TEXT NOT NULL,
            UNIQUE (workflow_id, batch_id, row_number)
        );

        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            call_id TEXT,
            occurred_at_utc TEXT NOT NULL,
            phone_hash TEXT,
            payload_json TEXT NOT NULL,
            file_id INTEGER NOT NULL REFERENCES ingested_files(id)
        );

        CREATE INDEX IF NOT EXISTS idx_events_call_id
            ON events (call_id);
        CREATE INDEX IF NOT EXISTS idx_events_workflow_phone_hash
            ON events (workflow_id, phone_hash);

        CREATE TRIGGER IF NOT EXISTS events_no_update
        BEFORE UPDATE ON events
        BEGIN
            SELECT RAISE(ABORT, 'events are append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS events_no_delete
        BEFORE DELETE ON events
        BEGIN
            SELECT RAISE(ABORT, 'events are append-only');
        END;
        """
    )
