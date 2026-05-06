from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .models import ChangeEvent, ChangeType, Snapshot


POSITION_FIELDS_TO_COMPARE = ("volume", "price_open", "sl", "tp", "magic", "comment")
ORDER_FIELDS_TO_COMPARE = (
    "volume_initial",
    "volume_current",
    "price_open",
    "sl",
    "tp",
    "time_expiration",
    "magic",
    "comment",
)


def detect_changes(
    previous_positions: Snapshot,
    current_positions: Snapshot,
    previous_orders: Snapshot,
    current_orders: Snapshot,
) -> list[ChangeEvent]:
    events: list[ChangeEvent] = []
    events.extend(
        _detect_table_changes(
            previous_positions,
            current_positions,
            created_type=ChangeType.POSITION_OPENED,
            updated_type=ChangeType.POSITION_UPDATED,
            deleted_type=ChangeType.POSITION_CLOSED,
            comparable_fields=POSITION_FIELDS_TO_COMPARE,
        )
    )
    events.extend(
        _detect_table_changes(
            previous_orders,
            current_orders,
            created_type=ChangeType.ORDER_CREATED,
            updated_type=ChangeType.ORDER_UPDATED,
            deleted_type=ChangeType.ORDER_DELETED,
            comparable_fields=ORDER_FIELDS_TO_COMPARE,
        )
    )
    return events


def _detect_table_changes(
    previous: Snapshot,
    current: Snapshot,
    created_type: ChangeType,
    updated_type: ChangeType,
    deleted_type: ChangeType,
    comparable_fields: Iterable[str],
) -> list[ChangeEvent]:
    events: list[ChangeEvent] = []

    for ticket, row in current.items():
        if ticket not in previous:
            events.append(_event(created_type, ticket, None, row, {}))
            continue

        changed_fields = _changed_fields(previous[ticket], row, comparable_fields)
        if changed_fields:
            events.append(_event(updated_type, ticket, previous[ticket], row, changed_fields))

    for ticket, row in previous.items():
        if ticket not in current:
            events.append(_event(deleted_type, ticket, row, None, {}))

    return events


def _changed_fields(
    previous: dict[str, Any],
    current: dict[str, Any],
    fields: Iterable[str],
) -> dict[str, dict[str, Any]]:
    changed: dict[str, dict[str, Any]] = {}
    for field in fields:
        previous_value = previous.get(field)
        current_value = current.get(field)
        if previous_value != current_value:
            changed[field] = {"from": previous_value, "to": current_value}
    return changed


def _event(
    change_type: ChangeType,
    ticket: str,
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
    changed_fields: dict[str, dict[str, Any]],
) -> ChangeEvent:
    row = current or previous or {}
    return ChangeEvent(
        change_type=change_type,
        source_ticket=ticket,
        symbol=str(row.get("symbol", "")),
        trade_type=str(row.get("type", "")),
        previous=previous,
        current=current,
        changed_fields=changed_fields,
    )
