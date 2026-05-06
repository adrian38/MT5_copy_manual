from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []

    last_error: PermissionError | None = None
    for _attempt in range(5):
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as fh:
                sample = fh.read(2048)
                fh.seek(0)
                dialect = _detect_dialect(sample)
                return [dict(row) for row in csv.DictReader(fh, dialect=dialect)]
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05)

    if last_error is not None:
        raise last_error
    return []


def read_latest_row(path: Path) -> dict[str, Any] | None:
    rows = read_csv_rows(path)
    if not rows:
        return None
    return rows[-1]


def rows_to_snapshot(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticket = str(row.get("ticket", "")).strip()
        if ticket:
            snapshot[ticket] = _normalize_row(row)
    return snapshot


def _detect_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters="\t,;")
    except csv.Error:
        return csv.excel_tab


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in row.items():
        if key is None:
            continue
        text = "" if value is None else str(value).strip()
        normalized[key.strip()] = _normalize_value(text)
    return normalized


def _normalize_value(value: str) -> Any:
    if value == "":
        return ""
    try:
        if "." in value:
            return round(float(value), 10)
        return int(value)
    except ValueError:
        return value
