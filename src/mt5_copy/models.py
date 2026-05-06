from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class ChangeType(str, Enum):
    POSITION_OPENED = "position_opened"
    POSITION_UPDATED = "position_updated"
    POSITION_CLOSED = "position_closed"
    ORDER_CREATED = "order_created"
    ORDER_UPDATED = "order_updated"
    ORDER_DELETED = "order_deleted"


@dataclass(frozen=True)
class ChangeEvent:
    change_type: ChangeType
    source_ticket: str
    symbol: str
    trade_type: str
    previous: dict[str, Any] | None
    current: dict[str, Any] | None
    changed_fields: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["change_type"] = self.change_type.value
        return data


Snapshot = dict[str, dict[str, Any]]
