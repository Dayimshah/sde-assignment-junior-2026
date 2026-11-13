# Submission

## Candidate
- **Name:** Dayim Shah
- **Assignment chosen:** A (Data)
- **Time spent (honest estimate):** 2 hrs

## How to run

Run these from the repo root. Python 3.11+ is required. Only the standard library is used at
runtime. pytest comes from `requirements.txt`.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

cd solution
python -m ledger ingest ../data/drop_1
python -m ledger ingest ../data/drop_2
python -m ledger report --out ../reports

python -m pytest
```

The database defaults to `solution/ledger.db`. Pass `--db <file>` to use another path.
Set `AU_PII_KEY` in the environment to choose the phone-hash key. If it is unset, a random key is
written to `<db>.key` on first use, and you need that file to keep matching phones across runs.

## Design

Code lives in `solution/ledger/`. It has four layers:

- **Normalisation** (`normalise.py`, `catalog.py`): header matching, phone, date, amount and enum
  rules, and the lead schemas and disposition catalogue, loaded from `ledger/schemas/`.
- **Ingestion** (`leads.py`, `events.py`, `ingest.py`): writes to the database. Each drop is one
  transaction. Each file is recorded with its SHA-256, so re-ingesting an unchanged file is a
  no-op. Changed content under a known name is an error.
- **Storage** (`store.py`): SQLite with five tables. `leads` holds one row per workflow and phone.
  `rejections` holds one row per rejected lead row. `events` is the append-only ledger, keyed by
  `event_id`. `ingested_files` and `stats` hold bookkeeping.
- **Reporting** (`reports.py`): derives everything from the current database state. It links each
  event to a lead, groups events into calls, works out effective dispositions and buckets, and
  writes the five files.

Lead table: `(workflow_id, phone_hash)` is unique. The first valid row sets `batch_id`. Later
rows overwrite only non-null values. Non-PII fields are kept as JSON in `fields`, and
`external_ref` is a separate column.

Event table: `event_id` is the primary key. Linking is not stored. A report pass resolves it:
- an event with a valid phone links to `(workflow_id, phone_hash)` if that lead exists;
- a `disposition.override` links through its `call_id` to the lead of that call's
  `call.initiated` event;
- anything else is an orphan.

Because linking is computed on every report, an event that arrives before its lead starts
counting as soon as the lead is ingested. Nothing has to be re-ingested.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Link events at report time, not ingest time | Orphans must count once their lead arrives. Linking at report time handles that with no backfill. | Link at ingest and re-link on each new lead. More code and more state to keep right. |
| Count orphans and unknown dispositions at report time | Keeps the rule "orphan first, so not checked for disposition" exact. An event that is both orphan and unknown is only an orphan. | Count unknown at ingest. That would count some events that are orphans. |
| Do not store customer names at all | No report needs them. Dropping them is safer than encrypting them. | Encrypt with the same key as the phone hash. |
| Phone stored as HMAC-SHA256 digest, last four digits in clear | Matching needs the digest. `phone_masked` needs the last four digits. | Store the full phone encrypted, then decrypt to match. Slower and needs key handling for decryption. |
| Key from `AU_PII_KEY`, else random key file next to the DB | Works with no setup. Keeps the key out of the code and out of git. | Require the env var. Harder to run from a clean clone. |
| Override links only through `call_id`, even when it carries a phone | The brief says overrides link through their call. | Use the phone when present. Gives the same answer for generated data, but goes against the brief. |
| Only malformed JSON, non-object lines, missing or non-string `event_id`, and unparseable `occurred_at` count as malformed | The brief names malformed as "not valid JSON or no `event_id`". An unparseable timestamp cannot be placed in time, so I treat it the same way. | Store it with a null time. Would break ordering rules. |
| Blank lines in event files are skipped and not counted in `events_total` | The brief says "every non-empty line". | Count them. Goes against the wording. |
| Calls count only if the linked `call.initiated` event exists | Brief rule. | None. |
| Events in a call count toward that call only when they link to the same lead as its `call.initiated` | Keeps one call tied to one lead. Never happens in generated data. | Count any linked event. |
| Defaults applied after each merge, to null fields | Gives the same result as applying them once after all merges, since later non-null values overwrite. | Apply at the end only. Needs a second pass. |
| Non-phone required field with an invalid value becomes `invalid_<field>` | The brief does not cover this. The shipped schemas have only string required fields, so it never fires. | Accept the value. |
| If two columns map to the same field, the first wins | The brief does not say. | Reject the row or merge. |
| Rounding amounts half-up to 2 places | Standard for money. | Banker's rounding. |
| Event ordering uses `(occurred_at UTC, event_id)` | Stable on ties. | Use arrival order. Would depend on file layout. |
| `last_call_at` and the current disposition use the call's `initiated` time | Brief says "most recent call (by initiated time)". | Use `ended` time. |
| Output CSV lines end with `\n` | Allowed by the brief. | `\r\n`. |

## Failure modes considered

- **Crash mid-drop.** Each drop is one transaction, so a crash rolls it back. Re-running the drop
  then starts from a clean state.
- **Re-run of an ingested drop.** Each file's SHA-256 is recorded, so the file is skipped. Reports
  are byte-identical after a re-run (checked on the generated data).
- **Same file name, different content.** Refused with an error, instead of double counting.
- **Duplicate events across drops.** Caught by the `event_id` primary key. Counted as duplicates.
- **Duplicate leads within or across batches.** Merged by `(workflow_id, phone_hash)`. The first
  batch keeps ownership of the lead.
- **Events before their lead.** Stored as orphans. Linked on the next report after the lead
  arrives.
- **Overrides with no phone.** Linked through `call_id`. An override whose call has no linked
  `call.initiated` is an orphan.
- **Bad dispositions.** Counted and ignored. The call falls back to the latest valid set, or has
  no disposition.
- **Malformed lines.** Counted and skipped. Other lines in the file still load.
- **Lost phone key.** Phone matching fails for old data. The key file must be kept with the DB.

Known gaps:
- Two processes ingesting into the same database at once are not coordinated. SQLite will
  serialise them, but nothing guards the file-level check.
- The report loads all events into memory. Fine for the small set. The large set has not been
  run, so I cannot say how it behaves.
- Non-override events without a valid phone are always orphans. The brief does not say what to
  do with them, so I treated them that way.
- The `stats` table is incremented in the same transaction as the data, so the counts stay
  consistent with the data. The counts are not reset per report.

## Testing

- `solution/tests/test_normalise.py`: phone, date, amount and header normalisation, plus
  timestamp parsing.
- `solution/tests/test_pipeline.py`: rejection order, blank rows, merging and defaults, invalid
  optional fields, PII not in plaintext, orphan late-arrival, overrides, terminal and
  telephony-failed buckets, malformed and unknown events, idempotent re-ingest, split-versus-single
  ingest equivalence, and the output file contract.

Run with `python -m pytest` from `solution/`. 49 tests, all passing.

Not tested: the large dataset, concurrent runs, and the CLI through a subprocess. I ran the CLI
manually on the seed-42 data.

## Results

Seed 42, small scale, `drop_1` then `drop_2`:

| Figure | Value |
|---|---|
| Lead rows total | 2,057 |
| Rejected | 108 (invalid phone 42, missing name 25, missing phone 14, missing policy number 14, missing order id 13) |
| Duplicate rows merged | 57 |
| Leads | 1,892 |
| Field warnings | 122 |
| Events total | 8,745 (equal to the non-empty line count from the generator) |
| Malformed | 8 |
| Duplicate events dropped | 237 |
| Orphans at final state | 462 |
| Unknown dispositions (linked only) | 18 |

These are consistent with each other. The lead arithmetic and the event totals reconcile.

I did not time the large scale, so I have no timing figures to report.

## If I had another day

1. Run the large scale and measure. Move bucket and daily aggregation into SQL, which is a stretch
   goal in the brief.
2. Add a lock file or database-level check so two ingests cannot run at once.
3. Add tests for the CLI through a subprocess and for the key-file path.
4. Handle non-override events without a phone explicitly, so their handling is visible in the
   data quality report.
5. Think through the production version: a scheduler that picks up new drops, alerts on
   quarantine growth, and a backfill procedure that reuses the same idempotent ingest.
