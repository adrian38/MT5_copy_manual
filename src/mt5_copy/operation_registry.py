from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_OPERATION_ERROR_FILE = Path("data/state/operation_errors.json")
MAX_OPERATION_ATTEMPTS = 3


@dataclass(frozen=True)
class OperationErrorRecord:
    key: str
    operation: str
    source_ticket: str
    destination_ticket: str
    symbol: str
    trade_type: str
    attempts: int
    reason: str
    failed_at_utc: str


def operation_error_path(project_root: Path) -> Path:
    return project_root / DEFAULT_OPERATION_ERROR_FILE


def make_operation_key(operation: str, source_ticket: Any = "", destination_ticket: Any = "") -> str:
    return "|".join(
        [
            str(operation).strip(),
            str(source_ticket or "").strip(),
            str(destination_ticket or "").strip(),
        ]
    )


def load_operation_errors(path: Path) -> dict[str, OperationErrorRecord]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if isinstance(raw, list):
        records = raw
    else:
        records = raw.get("errors", [])

    loaded: dict[str, OperationErrorRecord] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        if not key:
            key = make_operation_key(
                item.get("operation", ""),
                item.get("source_ticket", ""),
                item.get("destination_ticket", ""),
            )
        loaded[key] = OperationErrorRecord(
            key=key,
            operation=str(item.get("operation", "")),
            source_ticket=str(item.get("source_ticket", "")),
            destination_ticket=str(item.get("destination_ticket", "")),
            symbol=str(item.get("symbol", "")),
            trade_type=str(item.get("trade_type", "")),
            attempts=int(item.get("attempts", MAX_OPERATION_ATTEMPTS) or MAX_OPERATION_ATTEMPTS),
            reason=str(item.get("reason", "")),
            failed_at_utc=str(item.get("failed_at_utc", "")),
        )
    return loaded


def save_operation_errors(path: Path, records: dict[str, OperationErrorRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"errors": [asdict(record) for record in records.values()]}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    tmp_path.replace(path)


def clear_operation_errors(path: Path) -> int:
    records = load_operation_errors(path)
    if not records:
        return 0
    save_operation_errors(path, {})
    return len(records)


def mark_operation_error(
    path: Path,
    *,
    operation: str,
    source_ticket: Any = "",
    destination_ticket: Any = "",
    symbol: Any = "",
    trade_type: Any = "",
    attempts: int = MAX_OPERATION_ATTEMPTS,
    reason: str,
    notify: Callable[[OperationErrorRecord], None] | None = None,
) -> OperationErrorRecord:
    key = make_operation_key(operation, source_ticket, destination_ticket)
    records = load_operation_errors(path)
    record = OperationErrorRecord(
        key=key,
        operation=str(operation),
        source_ticket=str(source_ticket or ""),
        destination_ticket=str(destination_ticket or ""),
        symbol=str(symbol or ""),
        trade_type=str(trade_type or ""),
        attempts=attempts,
        reason=reason,
        failed_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    records[key] = record
    save_operation_errors(path, records)
    if notify is not None:
        notify(record)
    return record


def is_operation_discarded(
    path: Path | None,
    *,
    operation: str | None = None,
    source_ticket: Any = "",
    destination_ticket: Any = "",
) -> bool:
    if path is None:
        return False
    records = load_operation_errors(path)
    source = str(source_ticket or "")
    destination = str(destination_ticket or "")
    expected_operation = str(operation) if operation is not None else ""

    for record in records.values():
        if expected_operation and record.operation != expected_operation:
            continue
        if source and record.source_ticket == source:
            return True
        if destination and record.destination_ticket == destination:
            return True
    return False
