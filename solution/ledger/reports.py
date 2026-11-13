"""Derives calls, effective dispositions and lead buckets from the ledger, then writes the five report files.

Everything is computed from the current state of the database, so events that arrived before
their lead are linked as soon as the lead exists.
"""
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .catalog import SCHEMA_DIR, load_dispositions, load_lead_schema
from .normalise import ist_date, ist_iso_seconds

BUCKETS = ("yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed")


@dataclass
class _CallEvent:
    lead_id: object
    event_type: str
    event_id: str
    occurred: str
    code: object


@dataclass
class _RawCall:
    call_id: str
    lead_id: object = None
    initiated: str = None
    events: list = field(default_factory=list)


@dataclass
class Call:
    call_id: str
    lead_id: int
    workflow_id: str
    initiated: str
    date_ist: str
    connected: bool
    failed: bool
    code: object
    is_terminal: bool
    is_success: bool


def _load_leads(store):
    leads = {}
    by_key = {}
    for row in store.conn.execute(
        "SELECT lead_id, workflow_id, phone_hash, phone_last4, batch_id, external_ref FROM leads"
    ):
        leads[row["lead_id"]] = dict(row)
        by_key[(row["workflow_id"], row["phone_hash"])] = row["lead_id"]
    return leads, by_key


def _resolve_events(store, leads, by_key, catalogue):
    """Link every event to a lead and group linked events by call.

    Returns (calls, orphan_count, unknown_disposition_count). Orphans are unlinked events. Unknown
    dispositions are counted only among linked events, because an orphan is counted as an orphan.
    """
    rows = store.conn.execute(
        "SELECT event_id, event_type, workflow_id, call_id, occurred_at, phone_hash, disposition_code "
        "FROM events ORDER BY occurred_at, event_id"
    ).fetchall()

    # Pass 1: events that carry a phone link through (workflow_id, phone_hash).
    # Overrides carry no phone, so they are left for pass 2.
    lead_of = {}
    call_lead = {}
    for row in rows:
        if row["event_type"] == "disposition.override":
            continue
        lead_id = None
        if row["phone_hash"] is not None:
            lead_id = by_key.get((row["workflow_id"], row["phone_hash"]))
        lead_of[row["event_id"]] = lead_id
        if lead_id is not None and row["event_type"] == "call.initiated" and row["call_id"]:
            call_lead.setdefault(row["call_id"], lead_id)

    # Pass 2: an override links through its call to the lead of that call.
    for row in rows:
        if row["event_type"] == "disposition.override":
            lead_of[row["event_id"]] = call_lead.get(row["call_id"])

    orphans = 0
    unknown = 0
    raw_calls = {}
    for row in rows:
        lead_id = lead_of[row["event_id"]]
        if lead_id is None:
            orphans += 1
            continue
        workflow_id = leads[lead_id]["workflow_id"]
        if row["event_type"] in ("disposition.set", "disposition.override"):
            if (workflow_id, row["disposition_code"]) not in catalogue:
                unknown += 1
                continue
        if not row["call_id"]:
            continue
        call = raw_calls.setdefault(row["call_id"], _RawCall(call_id=row["call_id"]))
        if row["event_type"] == "call.initiated" and call.initiated is None:
            call.lead_id = lead_id
            call.initiated = row["occurred_at"]
        call.events.append(_CallEvent(lead_id, row["event_type"], row["event_id"],
                                      row["occurred_at"], row["disposition_code"]))

    calls = []
    for raw in raw_calls.values():
        if raw.lead_id is None:
            continue
        connected = failed = False
        sets, overrides = [], []
        for ev in raw.events:
            if ev.lead_id != raw.lead_id:
                continue
            if ev.event_type == "call.connected":
                connected = True
            elif ev.event_type == "call.telephony_failed":
                failed = True
            elif ev.event_type == "disposition.set":
                sets.append((ev.occurred, ev.event_id, ev.code))
            elif ev.event_type == "disposition.override":
                overrides.append((ev.occurred, ev.event_id, ev.code))
        pool = overrides or sets
        code = max(pool)[2] if pool else None
        workflow_id = leads[raw.lead_id]["workflow_id"]
        terminal = success = False
        if code is not None:
            disposition = catalogue[(workflow_id, code)]
            terminal, success = disposition.is_terminal, disposition.is_success
        calls.append(Call(
            call_id=raw.call_id,
            lead_id=raw.lead_id,
            workflow_id=workflow_id,
            initiated=raw.initiated,
            date_ist=ist_date(datetime.fromisoformat(raw.initiated)),
            connected=connected,
            failed=failed,
            code=code,
            is_terminal=terminal,
            is_success=success,
        ))
    return calls, orphans, unknown


def _bucket(calls):
    if not calls:
        return "yet_to_reach_out"
    if any(c.is_terminal for c in calls):
        return "completed"
    if all(c.failed for c in calls):
        return "in_progress_telephony_failed"
    return "in_progress_dialed"


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _flag(value):
    return "true" if value else "false"


def compute(store):
    """Return every report as plain Python rows, plus data_quality.json as a dict."""
    catalogue = load_dispositions()
    leads, by_key = _load_leads(store)
    calls, orphans, unknown = _resolve_events(store, leads, by_key, catalogue)

    calls_by_lead = defaultdict(list)
    for call in calls:
        calls_by_lead[call.lead_id].append(call)

    bucket_of = {lead_id: _bucket(calls_by_lead[lead_id]) for lead_id in leads}

    # batch_summary.csv
    batch_counts = defaultdict(Counter)
    for lead_id, lead in leads.items():
        counts = batch_counts[(lead["workflow_id"], lead["batch_id"])]
        counts["total_leads"] += 1
        counts[bucket_of[lead_id]] += 1
    batch_rows = []
    for (workflow_id, batch_id) in sorted(batch_counts):
        c = batch_counts[(workflow_id, batch_id)]
        batch_rows.append([workflow_id, batch_id, c["total_leads"], c["yet_to_reach_out"],
                           c["in_progress_telephony_failed"], c["in_progress_dialed"], c["completed"]])

    # daily_overview.csv
    daily = {}
    for call in calls:
        day = daily.setdefault((call.workflow_id, call.date_ist),
                               {"total": 0, "connected": 0, "success": set()})
        day["total"] += 1
        day["connected"] += int(call.connected)
        if call.is_success:
            day["success"].add(call.lead_id)
    daily_rows = []
    for (workflow_id, date) in sorted(daily):
        day = daily[(workflow_id, date)]
        rate = f"{day['connected'] / day['total']:.4f}"
        daily_rows.append([workflow_id, date, day["total"], day["connected"], rate, len(day["success"])])

    # lead_report.csv
    lead_rows = []
    order = sorted(leads, key=lambda k: (leads[k]["workflow_id"], leads[k]["external_ref"], k))
    for lead_id in order:
        lead = leads[lead_id]
        lead_calls = calls_by_lead[lead_id]
        last_call = max((c.initiated for c in lead_calls), default=None)
        with_code = [c for c in lead_calls if c.code is not None]
        current = max(with_code, key=lambda c: (c.initiated, c.call_id)).code if with_code else ""
        lead_rows.append([
            lead["workflow_id"],
            lead["external_ref"],
            lead["batch_id"],
            f"+91******{lead['phone_last4']}",
            bucket_of[lead_id],
            len(lead_calls),
            sum(1 for c in lead_calls if c.connected),
            ist_iso_seconds(datetime.fromisoformat(last_call)) if last_call else "",
            current or "",
            _flag(any(c.is_success for c in lead_calls)),
        ])

    # rejections.csv
    rejection_rows = [
        [r["workflow_id"], r["batch_id"], r["row_number"], r["reason"]]
        for r in store.conn.execute(
            "SELECT workflow_id, batch_id, row_number, reason FROM rejections"
        ).fetchall()
    ]
    rejection_rows.sort(key=lambda r: (r[0], r[1], r[2]))

    # data_quality.json
    stats = store.stats()
    reason_counts = Counter(r[3] for r in rejection_rows)
    # Every reason the schemas can produce appears, even at zero, followed by any other reason seen.
    known = ["invalid_phone"]
    for path in sorted(SCHEMA_DIR.glob("*.json")):
        schema = load_lead_schema(path.stem)
        known += [f"missing_required:{f.name}" for f in schema.fields if f.required]
    by_reason = {reason: reason_counts.get(reason, 0)
                 for reason in dict.fromkeys(known + sorted(reason_counts))}

    quality = {
        "lead_rows_total": stats.get("lead_rows_total", 0),
        "lead_rows_rejected": len(rejection_rows),
        "rejections_by_reason": by_reason,
        "duplicate_lead_rows_merged": stats.get("duplicate_lead_rows_merged", 0),
        "field_warnings": stats.get("field_warnings", 0),
        "events_total": stats.get("events_total", 0),
        "malformed_events": stats.get("malformed_events", 0),
        "duplicate_events_dropped": stats.get("duplicate_events_dropped", 0),
        "orphan_events": orphans,
        "unknown_disposition_events": unknown,
    }

    return {
        "batch_summary": batch_rows,
        "daily_overview": daily_rows,
        "lead_report": lead_rows,
        "rejections": rejection_rows,
        "data_quality": quality,
    }


def write_reports(store, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = compute(store)

    _write_csv(out / "batch_summary.csv",
               ["workflow_id", "batch_id", "total_leads", "yet_to_reach_out",
                "in_progress_telephony_failed", "in_progress_dialed", "completed"],
               result["batch_summary"])
    _write_csv(out / "daily_overview.csv",
               ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate",
                "success_leads"],
               result["daily_overview"])
    _write_csv(out / "lead_report.csv",
               ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls",
                "connected_calls", "last_call_at", "current_disposition", "ever_success"],
               result["lead_report"])
    _write_csv(out / "rejections.csv",
               ["workflow_id", "batch_id", "row_number", "reason"],
               result["rejections"])
    with open(out / "data_quality.json", "w", encoding="utf-8") as fh:
        json.dump(result["data_quality"], fh, indent=2)
        fh.write("\n")
    return result
