from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EMPTY_STATE = {"positions": {}, "orders": {}}


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return EMPTY_STATE.copy()
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return {
        "positions": dict(data.get("positions", {})),
        "orders": dict(data.get("orders", {})),
    }


def save_state(path: Path, positions: dict[str, Any], orders: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"positions": positions, "orders": orders}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    tmp_path.replace(path)
