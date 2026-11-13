# Solution: Assignment A (Interaction Ledger & Reporting Pipeline)

The code is in `ledger/`. Tests are in `tests/`. See `../SUBMISSION.md` for the design, decisions,
and results.

## Layout

```
ledger/
  __main__.py     entry point: python -m ledger
  cli.py          ingest and report commands
  ingest.py       drop-level transaction and file bookkeeping
  leads.py        lead header mapping, row validation, merging
  events.py       event line parsing, dedupe, ledger writes
  normalise.py    phone, date, amount, enum and timestamp rules
  catalog.py      lead schemas and disposition catalogue
  store.py        SQLite schema, PII key and hashing
  reports.py      linking, calls, buckets, and the five output files
  schemas/        copies of assignment-a-data/schemas
tests/
```

## Run

From the repo root:

```bash
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
cd solution
python -m ledger ingest ../data/drop_1
python -m ledger ingest ../data/drop_2
python -m ledger report --out ../reports
python -m pytest
```

Set `AU_PII_KEY` to choose the phone-hash key. Otherwise a key is created in `ledger.db.key`.
Keep that file with the database.
