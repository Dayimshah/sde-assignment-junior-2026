"""SQLite storage: lead table, rejections, append-only event ledger, and ingest bookkeeping.

PII handling:
- Customer names are not stored at all. No report needs them.
- Phone numbers are stored only as an HMAC-SHA256 digest keyed with a secret. The digest is
  enough to match events to leads. The last four digits are kept in clear because
  `phone_masked` needs them.
- The HMAC key comes from the AU_PII_KEY environment variable. If that is unset, a random key is
  generated once and saved next to the database (`<db>.key`). Losing that file means phone
  matching cannot work against an existing database.
"""
import hashlib
import hmac
import json
import os
import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ingested_files (
    drop_name TEXT NOT NULL,
    rel_path  TEXT NOT NULL,
    sha256    TEXT NOT NULL,
    PRIMARY KEY (drop_name, rel_path)
);

CREATE TABLE IF NOT EXISTS leads (
    lead_id      INTEGER PRIMARY KEY,
    workflow_id  TEXT NOT NULL,
    phone_hash   TEXT NOT NULL,
    phone_last4  TEXT NOT NULL,
    batch_id     TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    fields       TEXT NOT NULL,
    UNIQUE (workflow_id, phone_hash)
);

CREATE TABLE IF NOT EXISTS rejections (
    workflow_id TEXT NOT NULL,
    batch_id    TEXT NOT NULL,
    row_number  INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    PRIMARY KEY (workflow_id, batch_id, row_number)
);

CREATE TABLE IF NOT EXISTS events (
    event_id         TEXT PRIMARY KEY,
    drop_name        TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    workflow_id      TEXT,
    call_id          TEXT,
    occurred_at      TEXT NOT NULL,
    phone_hash       TEXT,
    disposition_code TEXT,
    payload          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_call ON events (call_id);

CREATE TABLE IF NOT EXISTS stats (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
"""


class IngestError(Exception):
    pass


def load_pii_key(db_path):
    env_key = os.environ.get("AU_PII_KEY")
    if env_key:
        return env_key.encode("utf-8")
    key_path = Path(str(db_path) + ".key")
    if not key_path.exists():
        key_path.write_text(os.urandom(32).hex(), encoding="utf-8")
    return bytes.fromhex(key_path.read_text(encoding="utf-8").strip())


class Store:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self._key = load_pii_key(self.db_path)

    def close(self):
        self.conn.close()

    def phone_hash(self, canonical_phone):
        return hmac.new(self._key, canonical_phone.encode("utf-8"), hashlib.sha256).hexdigest()

    def bump(self, key, amount=1):
        self.conn.execute(
            "INSERT INTO stats (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = value + excluded.value",
            (key, amount),
        )

    def stats(self):
        rows = self.conn.execute("SELECT key, value FROM stats").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def check_file_unchanged(self, drop_name, rel_path, sha256):
        """Return True if this exact file was already ingested. Raise if a known path has new content."""
        row = self.conn.execute(
            "SELECT sha256 FROM ingested_files WHERE drop_name = ? AND rel_path = ?",
            (drop_name, rel_path),
        ).fetchone()
        if row is None:
            return False
        if row["sha256"] != sha256:
            raise IngestError(f"{drop_name}/{rel_path} changed since it was ingested; refusing to double count")
        return True

    def mark_file_ingested(self, drop_name, rel_path, sha256):
        self.conn.execute(
            "INSERT INTO ingested_files (drop_name, rel_path, sha256) VALUES (?, ?, ?)",
            (drop_name, rel_path, sha256),
        )

    @staticmethod
    def dumps(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
