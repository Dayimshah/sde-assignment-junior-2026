"""Loads the lead schemas and the disposition catalogue shipped in ledger/schemas."""
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from .normalise import normalise_header

SCHEMA_DIR = Path(__file__).parent / "schemas"


@dataclass(frozen=True)
class LeadField:
    name: str
    type: str
    required: bool
    pii: bool
    reference: bool
    aliases: tuple
    values: tuple = ()
    default: str = None


@dataclass(frozen=True)
class LeadSchema:
    workflow_id: str
    fields: tuple

    def field(self, name):
        return next(f for f in self.fields if f.name == name)

    @property
    def reference_field(self):
        return next(f for f in self.fields if f.reference)

    def header_lookup(self):
        """Normalised header text -> field name. Headers missing from this map are ignored."""
        lookup = {}
        for f in self.fields:
            for label in (f.name, *f.aliases):
                lookup.setdefault(normalise_header(label), f.name)
        return lookup


@dataclass(frozen=True)
class Disposition:
    is_terminal: bool
    is_success: bool


def load_lead_schema(workflow_id):
    path = SCHEMA_DIR / f"{workflow_id}.json"
    if not path.exists():
        raise ValueError(f"no lead schema for workflow {workflow_id!r}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    fields = []
    for spec in raw["fields"]:
        fields.append(LeadField(
            name=spec["name"],
            type=spec["type"],
            required=bool(spec.get("required", False)),
            pii=bool(spec.get("pii", False)),
            reference=bool(spec.get("reference", False)),
            aliases=tuple(spec.get("aliases", [])),
            values=tuple(v.lower() for v in spec.get("values", [])),
            default=spec.get("default"),
        ))
    return LeadSchema(workflow_id=raw["workflow_id"], fields=tuple(fields))


def load_dispositions():
    catalogue = {}
    with open(SCHEMA_DIR / "dispositions.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row["workflow_id"], row["code"])
            catalogue[key] = Disposition(
                is_terminal=row["is_terminal"].strip().lower() == "true",
                is_success=row["is_success"].strip().lower() == "true",
            )
    return catalogue
