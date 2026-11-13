"""Lead file ingestion: header mapping, row validation, rejection and merging."""
import csv
import json

from .catalog import load_lead_schema
from .normalise import normalise_date, normalise_decimal, normalise_enum, normalise_header, normalise_phone


class RowRejected(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def map_columns(header, schema):
    """Return {field name: column index} for every header that matches a schema field.

    If two columns match the same field, the first one wins.
    """
    lookup = schema.header_lookup()
    column_of = {}
    for index, name in enumerate(header):
        field_name = lookup.get(normalise_header(name))
        if field_name is not None and field_name not in column_of:
            column_of[field_name] = index
    return column_of


def _convert(field, text):
    if field.type == "date":
        return normalise_date(text)
    if field.type == "decimal":
        return normalise_decimal(text)
    if field.type == "enum":
        return normalise_enum(text, field.values)
    return text


def parse_row(schema, cells, column_of):
    """Validate one row in schema order. Returns (values, field_warnings) or raises RowRejected."""
    values = {}
    warnings = 0
    for field in schema.fields:
        index = column_of.get(field.name)
        text = cells[index].strip() if index is not None and index < len(cells) else ""

        if not text:
            if field.required:
                raise RowRejected(f"missing_required:{field.name}")
            values[field.name] = None
            continue

        if field.type == "phone":
            canonical = normalise_phone(text)
            if canonical is None:
                raise RowRejected("invalid_phone")
            values[field.name] = canonical
            continue

        value = _convert(field, text)
        if value is None:
            if field.required:
                raise RowRejected(f"invalid_{field.name}")
            warnings += 1
        values[field.name] = value
    return values, warnings


def _apply_defaults(schema, fields):
    for field in schema.fields:
        if field.default is not None and fields.get(field.name) is None:
            fields[field.name] = field.default


def _upsert_lead(store, schema, batch_id, values):
    phone_field = next(f for f in schema.fields if f.type == "phone")
    reference = schema.reference_field.name
    canonical = values[phone_field.name]
    phone_hash = store.phone_hash(canonical)

    # Only non-PII, non-phone fields are kept in the lead record.
    incoming = {f.name: values[f.name] for f in schema.fields if not f.pii and f.type != "phone"}

    row = store.conn.execute(
        "SELECT lead_id, fields FROM leads WHERE workflow_id = ? AND phone_hash = ?",
        (schema.workflow_id, phone_hash),
    ).fetchone()

    if row is None:
        merged = dict(incoming)
        _apply_defaults(schema, merged)
        store.conn.execute(
            "INSERT INTO leads (workflow_id, phone_hash, phone_last4, batch_id, external_ref, fields) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (schema.workflow_id, phone_hash, canonical[-4:], batch_id, merged[reference],
             store.dumps(merged)),
        )
        return

    # Later rows overwrite only the fields they actually provide.
    merged = json.loads(row["fields"])
    for name, value in incoming.items():
        if value is not None:
            merged[name] = value
    _apply_defaults(schema, merged)
    store.conn.execute(
        "UPDATE leads SET external_ref = ?, fields = ? WHERE lead_id = ?",
        (merged[reference], store.dumps(merged), row["lead_id"]),
    )
    store.bump("duplicate_lead_rows_merged")


def ingest_lead_file(store, path, workflow_id):
    schema = load_lead_schema(workflow_id)
    batch_id = path.stem

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        column_of = map_columns(header, schema)

        row_number = 0
        for cells in reader:
            if not any(cell.strip() for cell in cells):
                continue
            row_number += 1
            store.bump("lead_rows_total")
            try:
                values, warnings = parse_row(schema, cells, column_of)
            except RowRejected as rejected:
                store.conn.execute(
                    "INSERT INTO rejections (workflow_id, batch_id, row_number, reason) VALUES (?, ?, ?, ?)",
                    (workflow_id, batch_id, row_number, rejected.reason),
                )
                continue
            _upsert_lead(store, schema, batch_id, values)
            store.bump("field_warnings", warnings)
