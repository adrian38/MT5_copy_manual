from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


FIELDNAMES = [
    "source_ticket",
    "destination_ticket",
    "symbol",
    "type",
    "source_volume",
    "destination_volume",
    "status",
]


def ensure_mapping_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()


def load_mapping(path: Path) -> dict[str, dict[str, Any]]:
    ensure_mapping_file(path)
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = csv.DictReader(fh)
        return {str(row["source_ticket"]): dict(row) for row in rows if row.get("source_ticket")}


def upsert_mapping(path: Path, row: dict[str, Any]) -> None:
    mapping = load_mapping(path)
    source_ticket = str(row["source_ticket"])
    mapping[source_ticket] = {field: row.get(field, "") for field in FIELDNAMES}

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(mapping.values())
