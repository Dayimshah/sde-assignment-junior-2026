"""Drop-level ingestion. Each drop is processed in one transaction, so a crash leaves nothing half done."""
import hashlib
from pathlib import Path

from .events import ingest_event_file
from .leads import ingest_lead_file


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ingest_drop(store, drop_dir):
    """Ingest one drop folder. Files already ingested with the same content are skipped.

    Lead files go first in filename order (batch_001, batch_002, ...), then event files in
    filename order. Drops must be ingested in the order they arrived.
    """
    drop_dir = Path(drop_dir)
    if not drop_dir.is_dir():
        raise FileNotFoundError(f"drop folder not found: {drop_dir}")
    drop_name = drop_dir.name

    lead_files = sorted(drop_dir.glob("leads/*/*.csv"), key=lambda p: (p.name, p.parent.name))
    event_files = sorted(drop_dir.glob("events/*.jsonl"), key=lambda p: p.name)

    summary = {"lead_files": 0, "event_files": 0, "skipped_files": 0}
    with store.conn:
        for path in lead_files:
            rel = path.relative_to(drop_dir).as_posix()
            if store.check_file_unchanged(drop_name, rel, _sha256(path)):
                summary["skipped_files"] += 1
                continue
            ingest_lead_file(store, path, workflow_id=path.parent.name)
            store.mark_file_ingested(drop_name, rel, _sha256(path))
            summary["lead_files"] += 1

        for path in event_files:
            rel = path.relative_to(drop_dir).as_posix()
            if store.check_file_unchanged(drop_name, rel, _sha256(path)):
                summary["skipped_files"] += 1
                continue
            ingest_event_file(store, path, drop_name)
            store.mark_file_ingested(drop_name, rel, _sha256(path))
            summary["event_files"] += 1

    return summary
