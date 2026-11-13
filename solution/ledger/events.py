"""Event file ingestion into the append-only ledger.

Only malformed lines and duplicates are decided here. Linking to leads happens at report time,
so an orphan event starts counting as soon as its lead is ingested.
"""
import json

from .normalise import normalise_phone, parse_timestamp, utc_key


def _parse_line(text):
    """Return (event, occurred_at as aware UTC datetime), or None if the line is malformed."""
    try:
        event = json.loads(text)
    except ValueError:
        return None
    if not isinstance(event, dict):
        return None
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return None
    occurred = event.get("occurred_at")
    if not isinstance(occurred, str):
        return None
    try:
        moment = parse_timestamp(occurred)
    except ValueError:
        return None
    return event, moment


def _insert(store, drop_name, event, moment):
    phone_hash = None
    raw_phone = event.get("phone")
    if isinstance(raw_phone, str):
        canonical = normalise_phone(raw_phone)
        if canonical is not None:
            phone_hash = store.phone_hash(canonical)

    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    disposition_code = payload.get("disposition_code")

    store.conn.execute(
        "INSERT INTO events (event_id, drop_name, event_type, workflow_id, call_id, occurred_at, "
        "phone_hash, disposition_code, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event["event_id"],
            drop_name,
            event.get("event_type"),
            event.get("workflow_id"),
            event.get("call_id"),
            utc_key(moment),
            phone_hash,
            disposition_code if isinstance(disposition_code, str) else None,
            store.dumps(payload),
        ),
    )


def ingest_event_file(store, path, drop_name):
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            store.bump("events_total")

            parsed = _parse_line(text)
            if parsed is None:
                store.bump("malformed_events")
                continue
            event, moment = parsed

            seen = store.conn.execute(
                "SELECT 1 FROM events WHERE event_id = ?", (event["event_id"],)
            ).fetchone()
            if seen is not None:
                store.bump("duplicate_events_dropped")
                continue

            _insert(store, drop_name, event, moment)
