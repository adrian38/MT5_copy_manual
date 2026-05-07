from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _get_project_root() -> Path:
    # When bundled with PyInstaller, resolve paths relative to the .exe
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _get_project_root()
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"


@dataclass(frozen=True)
class AppConfig:
    common_files_path: Path
    poll_seconds: float
    log_level: str
    positions_file: Path
    orders_file: Path
    heartbeat_file: Path
    destination_positions_file: Path
    destination_orders_file: Path
    destination_heartbeat_file: Path
    state_file: Path
    mapping_file: Path
    events_log_file: Path
    app_log_file: Path
    risk: dict[str, Any]
    notifications: dict[str, Any]
    executor: dict[str, Any]
    terminals: dict[str, Any]


def _project_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> AppConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    common_files_path = Path(raw["common_files_path"])
    files = raw["files"]
    terminals = dict(raw.get("terminals", {}))
    source_terminal = _selected_terminal(terminals, "source_terminal_id")
    destination_terminal = _selected_terminal(terminals, "destination_terminal_id")
    source_prefix = source_terminal.get("file_prefix", "") if source_terminal else ""
    destination_prefix = destination_terminal.get("file_prefix", "") if destination_terminal else ""
    executor = dict(raw.get("executor", {}))

    if destination_terminal and destination_terminal.get("window_title_contains"):
        executor["mt5_window_title_contains"] = destination_terminal["window_title_contains"]

    return AppConfig(
        common_files_path=common_files_path,
        poll_seconds=float(raw.get("poll_seconds", 1.0)),
        log_level=str(raw.get("log_level", "INFO")),
        positions_file=common_files_path / _prefixed_file(source_prefix, "positions", files["positions"]),
        orders_file=common_files_path / _prefixed_file(source_prefix, "orders", files["orders"]),
        heartbeat_file=common_files_path / _prefixed_file(source_prefix, "heartbeat", files["heartbeat"]),
        destination_positions_file=common_files_path
        / _prefixed_file(destination_prefix, "positions", files["destination_positions"]),
        destination_orders_file=common_files_path
        / _prefixed_file(destination_prefix, "orders", files["destination_orders"]),
        destination_heartbeat_file=common_files_path
        / _prefixed_file(destination_prefix, "heartbeat", files["destination_heartbeat"]),
        state_file=_project_path(files["state"]),
        mapping_file=_project_path(files["mapping"]),
        events_log_file=_project_path(files["events_log"]),
        app_log_file=_project_path(files["app_log"]),
        risk=dict(raw.get("risk", {})),
        notifications=dict(raw.get("notifications", {})),
        executor=executor,
        terminals=terminals,
    )


def _selected_terminal(terminals: dict[str, Any], selected_key: str) -> dict[str, Any]:
    selected_id = str(terminals.get(selected_key, ""))
    items = terminals.get("items", [])
    if not isinstance(items, list):
        return {}

    for item in items:
        if isinstance(item, dict) and str(item.get("id", "")) == selected_id:
            return dict(item)
    return {}


def _prefixed_file(prefix: str, kind: str, fallback: str) -> str:
    if not prefix:
        return fallback
    return f"{prefix}_{kind}.csv"
