from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    PROJECT_ROOT_FOR_SCRIPT = Path(__file__).resolve().parents[2]
    sys.path.append(str(PROJECT_ROOT_FOR_SCRIPT))
    from src.mt5_copy.config import PROJECT_ROOT
    from src.mt5_copy.config import DEFAULT_CONFIG_PATH, load_config
    from src.mt5_copy.csv_reader import read_csv_rows, read_latest_row, rows_to_snapshot
    from src.mt5_copy.detector import detect_changes
    from src.mt5_copy.event_log import append_events
    from src.mt5_copy.executor import build_executor, build_gui_controller
    from src.mt5_copy.logging_setup import setup_logging
    from src.mt5_copy.mapping import ensure_mapping_file
    from src.mt5_copy.models import ChangeEvent, ChangeType
    from src.mt5_copy.reconciler import reconcile_orders_to_source_authority, reconcile_sl_tp
    from src.mt5_copy.state import load_state, save_state
else:
    from .config import PROJECT_ROOT
    from .config import DEFAULT_CONFIG_PATH, load_config
    from .csv_reader import read_csv_rows, read_latest_row, rows_to_snapshot
    from .detector import detect_changes
    from .event_log import append_events
    from .executor import build_executor, build_gui_controller
    from .logging_setup import setup_logging
    from .mapping import ensure_mapping_file
    from .models import ChangeEvent, ChangeType
    from .reconciler import reconcile_orders_to_source_authority, reconcile_sl_tp
    from .state import load_state, save_state


def run_once(
    config_path: Path = DEFAULT_CONFIG_PATH,
    replay_current_orders: bool = False,
    replay_limit: int | None = None,
) -> int:
    config = load_config(config_path)
    logger = setup_logging(config.app_log_file, config.log_level)
    ensure_mapping_file(config.mapping_file)

    previous_state = load_state(config.state_file)
    positions = rows_to_snapshot(read_csv_rows(config.positions_file))
    orders = rows_to_snapshot(read_csv_rows(config.orders_file))
    heartbeat = read_latest_row(config.heartbeat_file)

    if replay_current_orders:
        order_items = list(orders.items())
        if replay_limit is not None:
            order_items = order_items[:replay_limit]

        events = [
            ChangeEvent(
                change_type=ChangeType.ORDER_CREATED,
                source_ticket=ticket,
                symbol=str(row.get("symbol", "")),
                trade_type=str(row.get("type", "")),
                previous=None,
                current=row,
                changed_fields={},
            )
            for ticket, row in order_items
        ]
        logger.warning("Replaying current pending orders as ORDER_CREATED events.")
    else:
        events = detect_changes(
            previous_positions=previous_state["positions"],
            current_positions=positions,
            previous_orders=previous_state["orders"],
            current_orders=orders,
        )

    append_events(config.events_log_file, events)
    executor = build_executor(
        mode=str(config.executor.get("mode", "dry_run")),
        pyautogui_enabled=bool(config.executor.get("pyautogui_enabled", False)),
        logger=logger,
        executor_settings=config.executor,
        project_root=PROJECT_ROOT,
        mapping_file=config.mapping_file,
        destination_orders_file=config.destination_orders_file,
    )

    for event in events:
        try:
            executor.handle(event)
        except Exception:
            logger.exception(
                "Event handling failed event=%s source_ticket=%s",
                event.change_type.value,
                event.source_ticket,
            )

    if (
        str(config.executor.get("mode", "dry_run")) == "pyautogui"
        and bool(config.executor.get("pyautogui_enabled", False))
        and bool(config.executor.get("authority_sync_enabled", False))
    ):
        gui = build_gui_controller(config.executor, PROJECT_ROOT, logger)
        report = reconcile_orders_to_source_authority(
            source_orders_file=config.orders_file,
            destination_orders_file=config.destination_orders_file,
            mapping_file=config.mapping_file,
            gui=gui,
            logger=logger,
        )
        if report.created or report.deleted or report.skipped or report.missing_sources or report.extra_destinations:
            logger.info("AUTHORITY_SYNC report=%s", report)

    save_state(config.state_file, positions, orders)
    logger.info(
        "Scan complete: positions=%s orders=%s events=%s heartbeat=%s",
        len(positions),
        len(orders),
        len(events),
        heartbeat,
    )
    return len(events)


def check_gui(
    config_path: Path = DEFAULT_CONFIG_PATH,
    take_screenshot: bool = False,
    list_windows: bool = False,
) -> None:
    config = load_config(config_path)
    logger = setup_logging(config.app_log_file, config.log_level)
    gui = build_gui_controller(config.executor, PROJECT_ROOT, logger)
    report = gui.check_environment()
    focused = gui.focus_mt5()
    report["focused"] = focused

    if take_screenshot:
        report["screenshot"] = str(gui.screenshot("manual_check"))

    if list_windows:
        report["windows"] = gui.list_windows()

    print(json.dumps(report, indent=2, sort_keys=True))


def run_reconcile(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    config = load_config(config_path)
    logger = setup_logging(config.app_log_file, config.log_level)
    gui = build_gui_controller(config.executor, PROJECT_ROOT, logger)
    remaining = reconcile_sl_tp(
        source_positions_file=config.positions_file,
        source_orders_file=config.orders_file,
        destination_positions_file=config.destination_positions_file,
        destination_orders_file=config.destination_orders_file,
        mapping_file=config.mapping_file,
        gui=gui,
        logger=logger,
    )
    print(json.dumps([issue.__dict__ for issue in remaining], indent=2, sort_keys=True))


def run_loop(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    config = load_config(config_path)
    logger = setup_logging(config.app_log_file, config.log_level)
    logger.info("Starting observer loop. Common Files: %s", config.common_files_path)

    while True:
        try:
            run_once(config_path)
        except KeyboardInterrupt:
            logger.info("Observer stopped by user.")
            raise
        except Exception:
            logger.exception("Unexpected error during observer scan.")
        time.sleep(config.poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read MT5 observer CSVs and detect changes.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--once", action="store_true", help="Run one scan and exit.")
    parser.add_argument(
        "--replay-current-orders",
        action="store_true",
        help="Treat all currently observed pending orders as ORDER_CREATED events.",
    )
    parser.add_argument(
        "--replay-limit",
        type=int,
        default=None,
        help="Maximum current pending orders to replay.",
    )
    parser.add_argument("--check-gui", action="store_true", help="Find and focus the MT5 window.")
    parser.add_argument("--screenshot", action="store_true", help="Save a screen capture during GUI check.")
    parser.add_argument("--list-windows", action="store_true", help="List visible window titles during GUI check.")
    parser.add_argument(
        "--reconcile-sl-tp",
        action="store_true",
        help="Reconcile SL/TP mismatches in destination orders and positions.",
    )
    parser.add_argument("--ui", action="store_true", help="Open the local monitoring UI.")
    args = parser.parse_args()

    if args.ui:
        if __package__ in {None, ""}:
            from src.mt5_copy.ui import run_ui
        else:
            from .ui import run_ui

        run_ui(args.config)
    elif args.reconcile_sl_tp:
        run_reconcile(args.config)
    elif args.check_gui:
        check_gui(
            args.config,
            take_screenshot=args.screenshot,
            list_windows=args.list_windows,
        )
    elif args.once:
        run_once(
            args.config,
            replay_current_orders=args.replay_current_orders,
            replay_limit=args.replay_limit,
        )
    else:
        run_loop(args.config)


if __name__ == "__main__":
    main()
