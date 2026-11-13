# AI Usage Disclosure

## Tools used
Kiro, an AI coding assistant in the Kiro IDE, running a Claude model. I used it in agent mode, so it
read and edited files and ran commands in my workspace.

## What I used them for
- Reading the assignment and proposing a plan.
- Writing the first draft of the `solution/ledger` package: header and value normalisation, the
  SQLite storage layer, lead and event ingestion, and the report code.
- Writing the first draft of the tests in `solution/tests/`.
- Writing the first draft of `SUBMISSION.md` and this file.

## What I wrote or decided myself
- I chose Assignment A and decided to use SQLite over Postgres, because the brief allows it and
  it needs no setup.
- I chose the PII approach: names are not stored, phones are stored as keyed HMAC digests, and
  only the last four digits are kept in clear for `phone_masked`.
- I chose to link events to leads at report time instead of at ingest time. That is what lets an
  orphan event start counting once its lead arrives in a later drop.
- I decided how to handle each spec gap and wrote the calls down in `SUBMISSION.md`.
- The AI checked the code against the brief's rules and checked the output on the generated
  seed-42 data. I have not yet reviewed it myself.

## Where the AI was wrong or unhelpful
- Two of my own tests were wrong. One keyed a dictionary on the batch column when it should have
  used the reference column. Another wrote to a database folder that did not exist. The pipeline
  was correct in both cases, and the failing tests showed it.
- The first draft of the report code had a leftover no-op loop and a redundant pass over event
  rows. I removed both after rereading the code.
- A test CSV row had an unquoted thousands separator, which split one cell into two. I fixed the
  fixture.

## How I verified AI-generated code
- 49 pytest tests. They cover phone, date, amount and header normalisation, rejection order,
  duplicate merging, defaults, orphan late-arrival, overrides, bucket logic, malformed and
  duplicate events, idempotent re-ingest, and split-versus-single ingest equivalence.
- I ran the full pipeline on the generated seed-42 small data for `drop_1` and `drop_2`. The
  totals reconcile: 2,057 lead rows minus 108 rejected minus 57 merged gives 1,892 leads, which
  matches the batch summary. Event totals match the line counts from the generator.
- I re-ran both drops on the same database. The reports were byte-identical to the first run.
- 