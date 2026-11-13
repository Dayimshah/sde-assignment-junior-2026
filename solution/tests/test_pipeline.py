import csv
import json

import pytest

from ledger.ingest import ingest_drop
from ledger.reports import compute, write_reports
from ledger.store import Store

LEAD_HEADER = "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language"


@pytest.fixture(autouse=True)
def fixed_pii_key(monkeypatch):
    monkeypatch.setenv("AU_PII_KEY", "test-key")


def write_leads(drop, batch, rows, header=LEAD_HEADER, workflow="motor_renewal"):
    path = drop / "leads" / workflow / f"{batch}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")


def write_events(drop, name, events):
    path = drop / "events" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) if isinstance(e, dict) else e for e in events) + "\n",
                    encoding="utf-8")


def event(eid, etype, call, at, phone=None, code=None, workflow="motor_renewal"):
    e = {"event_id": eid, "event_type": etype, "workflow_id": workflow, "call_id": call,
         "occurred_at": at, "payload": {}}
    if phone is not None:
        e["phone"] = phone
    if code is not None:
        e["payload"] = {"disposition_code": code}
    return e


def dial(eid_prefix, call, at, phone, outcome=None, code=None, workflow="motor_renewal"):
    """One connected call: initiated, connected, then a disposition. Times are IST."""
    events = [event(f"{eid_prefix}1", "call.initiated", call, at, phone, workflow=workflow)]
    if outcome == "failed":
        events.append(event(f"{eid_prefix}2", "call.telephony_failed", call, at, phone, workflow=workflow))
        return events
    events.append(event(f"{eid_prefix}2", "call.connected", call, at, phone, workflow=workflow))
    if code is not None:
        events.append(event(f"{eid_prefix}3", "disposition.set", call, at, phone, code, workflow=workflow))
    return events


def rows_of(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def open_store(tmp_path):
    return Store(tmp_path / "ledger.db")


def test_bad_rows_are_rejected_in_schema_order(tmp_path):
    drop = tmp_path / "drop_1"
    write_leads(drop, "batch_001", [
        "Asha Rao,9876543210,POL-1,,,",
        ",9876543211,POL-2,,,",              # missing name, valid phone
        ",5555555555,POL-3,,,",              # missing name AND invalid phone: name comes first
        "Ravi Nair,12345,POL-4,,,",          # invalid phone
        "Meena Iyer,9876543212,,,,",         # missing policy number
    ])
    store = open_store(tmp_path)
    ingest_drop(store, drop)
    rejections = store.conn.execute("SELECT row_number, reason FROM rejections ORDER BY row_number").fetchall()
    assert [(r["row_number"], r["reason"]) for r in rejections] == [
        (2, "missing_required:customer_name"),
        (3, "missing_required:customer_name"),
        (4, "invalid_phone"),
        (5, "missing_required:policy_number"),
    ]
    assert store.stats()["lead_rows_total"] == 5
    store.close()


def test_blank_rows_are_skipped_and_not_counted(tmp_path):
    drop = tmp_path / "drop_1"
    write_leads(drop, "batch_001", ["Asha Rao,9876543210,POL-1,,,", ",,,,,", "   ,  ,  ,,,", "Ravi Nair,9876543211,POL-2,,,"])
    store = open_store(tmp_path)
    ingest_drop(store, drop)
    assert store.stats()["lead_rows_total"] == 2
    store.close()


def test_duplicate_rows_merge_and_keep_first_batch(tmp_path):
    drop1 = tmp_path / "drop_1"
    drop2 = tmp_path / "drop_2"
    write_leads(drop1, "batch_001", ["Asha Rao,9876543210,POL-1,,,hi"])
    write_leads(drop2, "batch_003", ['Asha Rao,+91 98765 43210,POL-9,2026-12-01,"₹1,000",'])
    store = open_store(tmp_path)
    ingest_drop(store, drop1)
    ingest_drop(store, drop2)
    lead = store.conn.execute("SELECT batch_id, external_ref, fields FROM leads").fetchone()
    assert lead["batch_id"] == "batch_001"
    assert lead["external_ref"] == "POL-9"  # later non-null value wins
    fields = json.loads(lead["fields"])
    assert fields["premium_amount"] == "1000.00"
    assert fields["preferred_language"] == "hi"  # blank later value does not erase it
    assert store.stats()["duplicate_lead_rows_merged"] == 1
    store.close()


def test_defaults_apply_when_language_is_missing(tmp_path):
    drop = tmp_path / "drop_1"
    write_leads(drop, "batch_001", ["Asha Rao,9876543210,POL-1,,,"])
    store = open_store(tmp_path)
    ingest_drop(store, drop)
    fields = json.loads(store.conn.execute("SELECT fields FROM leads").fetchone()["fields"])
    assert fields["preferred_language"] == "en"
    store.close()


def test_invalid_optional_fields_become_null_with_warning(tmp_path):
    drop = tmp_path / "drop_1"
    write_leads(drop, "batch_001", ["Asha Rao,9876543210,POL-1,31/02/2026,N/A,xx"])
    store = open_store(tmp_path)
    ingest_drop(store, drop)
    fields = json.loads(store.conn.execute("SELECT fields FROM leads").fetchone()["fields"])
    assert fields["policy_expiry"] is None
    assert fields["premium_amount"] is None
    assert fields["preferred_language"] == "en"  # invalid enum falls back to default
    assert store.stats()["field_warnings"] == 3
    store.close()


def test_pii_is_not_stored_in_plaintext(tmp_path):
    drop = tmp_path / "drop_1"
    write_leads(drop, "batch_001", ["Asha Rao,9876543210,POL-1,,,"])
    store = open_store(tmp_path)
    ingest_drop(store, drop)
    raw = store.conn.execute("SELECT * FROM leads").fetchone()
    stored = " ".join(str(v) for v in dict(raw).values())
    assert "Asha" not in stored
    assert "9876543210" not in stored
    assert raw["phone_last4"] == "3210"
    store.close()


def test_orphan_event_counts_until_its_lead_arrives(tmp_path):
    drop1 = tmp_path / "drop_1"
    drop2 = tmp_path / "drop_2"
    write_leads(drop1, "batch_001", ["Other Person,9000000001,POL-0,,,"])
    write_events(drop1, "events_0001.jsonl",
                 dial("e1", "call_a", "2026-10-01 10:00:00", "9876543210", "connected", "renewed"))
    write_leads(drop2, "batch_003", ["Asha Rao,9876543210,POL-1,,,"])

    store = open_store(tmp_path)
    ingest_drop(store, drop1)
    assert compute(store)["data_quality"]["orphan_events"] == 3
    assert compute(store)["batch_summary"] == [["motor_renewal", "batch_001", 1, 1, 0, 0, 0]]

    ingest_drop(store, drop2)
    result = compute(store)
    assert result["data_quality"]["orphan_events"] == 0
    assert result["batch_summary"][-1] == ["motor_renewal", "batch_003", 1, 0, 0, 0, 1]
    store.close()


def test_override_replaces_effective_disposition_without_touching_original(tmp_path):
    drop = tmp_path / "drop_1"
    write_leads(drop, "batch_001", ["Asha Rao,9876543210,POL-1,,,"])
    events = dial("e", "call_a", "2026-10-01 10:00:00", "9876543210", "connected", "not_interested")
    events.append(event("ov1", "disposition.override", "call_a", "2026-10-02 09:00:00",
                        code="callback_requested"))
    write_events(drop, "events_0001.jsonl", events)

    store = open_store(tmp_path)
    ingest_drop(store, drop)
    lead = compute(store)["lead_report"][0]
    assert lead[4] == "in_progress_dialed"       # callback_requested is not terminal
    assert lead[8] == "callback_requested"
    original = store.conn.execute("SELECT payload FROM events WHERE event_id = 'e3'").fetchone()
    assert json.loads(original["payload"])["disposition_code"] == "not_interested"
    store.close()


def test_terminal_disposition_completes_lead_and_counts_success(tmp_path):
    drop = tmp_path / "drop_1"
    write_leads(drop, "batch_001", ["Asha Rao,9876543210,POL-1,,,"])
    write_events(drop, "events_0001.jsonl",
                 dial("e", "call_a", "2026-10-01 10:00:00", "9876543210", "connected", "renewed"))
    store = open_store(tmp_path)
    ingest_drop(store, drop)
    result = compute(store)
    assert result["lead_report"][0][4] == "completed"
    assert result["lead_report"][0][9] == "true"
    assert result["daily_overview"] == [["motor_renewal", "2026-10-01", 1, 1, "1.0000", 1]]
    store.close()


def test_telephony_failures_only_bucket_as_failed_when_every_call_failed(tmp_path):
    drop = tmp_path / "drop_1"
    write_leads(drop, "batch_001", ["Asha Rao,9876543210,POL-1,,,", "Ravi Nair,9876543211,POL-2,,,"])
    events = dial("a", "call_a", "2026-10-01 10:00:00", "9876543210", "failed")
    events += dial("b", "call_b", "2026-10-02 10:00:00", "9876543210", "failed")
    events += dial("c", "call_c", "2026-10-03 10:00:00", "9876543211", "failed")
    events += dial("d", "call_d", "2026-10-04 10:00:00", "9876543211", "connected", "callback_requested")
    write_events(drop, "events_0001.jsonl", events)
    store = open_store(tmp_path)
    ingest_drop(store, drop)
    buckets = {row[1]: row[4] for row in compute(store)["lead_report"]}
    assert buckets == {"POL-1": "in_progress_telephony_failed", "POL-2": "in_progress_dialed"}
    store.close()


def test_malformed_duplicate_and_unknown_events_are_counted(tmp_path):
    drop = tmp_path / "drop_1"
    write_leads(drop, "batch_001", ["Asha Rao,9876543210,POL-1,,,"])
    good = dial("e", "call_a", "2026-10-01 10:00:00", "9876543210", "connected", "renewed")
    unknown = event("e9", "disposition.set", "call_a", "2026-10-01 10:05:00", "9876543210", "RENEWD")
    events = good + [unknown, good[0], "{not json", '{"no_id": true}', ""]
    write_events(drop, "events_0001.jsonl", events)
    store = open_store(tmp_path)
    ingest_drop(store, drop)
    quality = compute(store)["data_quality"]
    assert quality["events_total"] == 7   # blank line is not counted
    assert quality["malformed_events"] == 2
    assert quality["duplicate_events_dropped"] == 1
    assert quality["unknown_disposition_events"] == 1
    assert quality["orphan_events"] == 0
    assert compute(store)["lead_report"][0][9] == "true"  # the renewed disposition still applies
    store.close()


def test_reingesting_a_drop_changes_nothing(tmp_path):
    drop = tmp_path / "drop_1"
    write_leads(drop, "batch_001", ["Asha Rao,9876543210,POL-1,,,"])
    write_events(drop, "events_0001.jsonl",
                 dial("e", "call_a", "2026-10-01 10:00:00", "9876543210", "connected", "renewed"))
    store = open_store(tmp_path)
    ingest_drop(store, drop)
    before = compute(store)
    summary = ingest_drop(store, drop)
    assert summary["skipped_files"] == 2
    assert compute(store) == before
    store.close()


def test_one_drop_at_a_time_matches_full_history(tmp_path):
    drop1 = tmp_path / "drop_1"
    drop2 = tmp_path / "drop_2"
    write_leads(drop1, "batch_001", ["Asha Rao,9876543210,POL-1,,,", "Ravi Nair,9876543211,POL-2,,,"])
    write_leads(drop2, "batch_003", ["Asha Rao,9876543210,POL-1,,,", "Ravi Nair,9876543211,POL-2,,,"])
    write_events(drop1, "events_0001.jsonl",
                 dial("a", "call_a", "2026-10-01 10:00:00", "9876543210", "connected", "not_interested"))
    write_events(drop2, "events_1001.jsonl",
                 dial("b", "call_b", "2026-10-02 10:00:00", "9876543211", "connected", "renewed"))

    inc_dir = tmp_path / "inc"
    inc_dir.mkdir()
    incremental = open_store(inc_dir)
    ingest_drop(incremental, drop1)
    ingest_drop(incremental, drop2)

    full_tmp = tmp_path / "full"
    full_tmp.mkdir()
    full = open_store(full_tmp)
    ingest_drop(full, drop1)
    ingest_drop(full, drop2)

    assert compute(incremental) == compute(full)
    incremental.close()
    full.close()


def test_report_files_match_output_contract(tmp_path):
    drop = tmp_path / "drop_1"
    write_leads(drop, "batch_001", ["Asha Rao,9876543210,POL-1,,,"])
    write_events(drop, "events_0001.jsonl",
                 dial("e", "call_a", "2026-10-01 10:00:00", "9876543210", "connected", "renewed"))
    store = open_store(tmp_path)
    ingest_drop(store, drop)
    out = tmp_path / "reports"
    write_reports(store, out)
    store.close()

    assert rows_of(out / "daily_overview.csv")[0] == [
        "workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"]
    assert rows_of(out / "lead_report.csv")[1] == [
        "motor_renewal", "POL-1", "batch_001", "+91******3210", "completed", "1", "1",
        "2026-10-01T10:00:00+05:30", "renewed", "true"]
    assert rows_of(out / "rejections.csv") == [["workflow_id", "batch_id", "row_number", "reason"]]
    quality = json.loads((out / "data_quality.json").read_text(encoding="utf-8"))
    assert list(quality) == [
        "lead_rows_total", "lead_rows_rejected", "rejections_by_reason", "duplicate_lead_rows_merged",
        "field_warnings", "events_total", "malformed_events", "duplicate_events_dropped",
        "orphan_events", "unknown_disposition_events"]
