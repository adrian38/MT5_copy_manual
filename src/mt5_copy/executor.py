from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .csv_reader import read_csv_rows
from .mapping import load_mapping, upsert_mapping
from .models import ChangeEvent, ChangeType
from .mt5_gui import GuiConfig, GuiSafetyError, Mt5GuiController


class DryRunExecutor:
    """Placeholder executor. It never opens, modifies, or closes trades."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def handle(self, event: ChangeEvent) -> None:
        self.logger.info(
            "DRY RUN event=%s source_ticket=%s symbol=%s type=%s changes=%s",
            event.change_type.value,
            event.source_ticket,
            event.symbol,
            event.trade_type,
            event.changed_fields,
        )


class PyAutoGuiExecutor:
    """Safe adapter for MT5 GUI automation. Trading actions are blocked unless armed."""

    def __init__(
        self,
        gui: Mt5GuiController,
        logger: logging.Logger,
        mapping_file: Path | None = None,
        source_orders_file: Path | None = None,
        destination_orders_file: Path | None = None,
        destination_positions_file: Path | None = None,
    ) -> None:
        self.gui = gui
        self.logger = logger
        self.mapping_file = mapping_file
        self.source_orders_file = source_orders_file
        self.destination_orders_file = destination_orders_file
        self.destination_positions_file = destination_positions_file

    def handle(self, event: ChangeEvent) -> None:
        if event.change_type == ChangeType.ORDER_CREATED:
            self._prepare_order_created(event)
            return

        if event.change_type == ChangeType.ORDER_DELETED:
            self._delete_order(event)
            return

        if event.change_type == ChangeType.POSITION_OPENED:
            self._prepare_market_position(event)
            return

        if event.change_type == ChangeType.POSITION_CLOSED:
            self._close_position(event)
            return

        if event.change_type in {
            ChangeType.ORDER_UPDATED,
            ChangeType.POSITION_UPDATED,
        }:
            self._log_unsupported_action(event)
            return

        self.logger.warning("Unhandled event type for GUI executor: %s", event.change_type)

    def _prepare_order_created(self, event: ChangeEvent) -> None:
        if not event.current:
            self.logger.warning("ORDER_CREATED event has no current row: %s", event.to_dict())
            return
        if _is_market_trade_type(event.trade_type):
            self.logger.info(
                "ORDER_CREATED ignored because it is a transient market row source_ticket=%s type=%s",
                event.source_ticket,
                event.trade_type,
            )
            return
        if self._order_created_is_already_handled(event):
            return

        before_tickets = self._destination_order_tickets()
        prepared = self.gui.prepare_pending_order(event.current)
        time.sleep(self.gui.config.order_window_delay_seconds)
        destination_ticket = self._find_new_destination_order_ticket(event.current, before_tickets)

        if self.mapping_file is not None and destination_ticket is not None:
            upsert_mapping(
                self.mapping_file,
                {
                    "source_ticket": event.source_ticket,
                    "destination_ticket": destination_ticket,
                    "symbol": event.current.get("symbol", ""),
                    "type": event.current.get("type", ""),
                    "source_volume": event.current.get("volume_current", event.current.get("volume_initial", "")),
                    "destination_volume": event.current.get("volume_current", event.current.get("volume_initial", "")),
                    "status": "placed",
                },
            )
        elif self.mapping_file is not None:
            self.logger.error(
                "ORDER_CREATED executed but destination ticket was not verified source_ticket=%s",
                event.source_ticket,
            )

        self.logger.info(
            "ORDER_CREATED prepared source_ticket=%s destination_ticket=%s symbol=%s type=%s volume=%s price=%s sl=%s tp=%s screenshot=%s",
            prepared["source_ticket"],
            destination_ticket or "",
            prepared["symbol"],
            prepared["type"],
            prepared["volume"],
            prepared["price_open"],
            prepared["sl"],
            prepared["tp"],
            prepared["screenshot_order_window"],
        )

    def _order_created_is_already_handled(self, event: ChangeEvent) -> bool:
        if self.mapping_file is None or self.destination_orders_file is None or not event.current:
            return False

        mapping = load_mapping(self.mapping_file)
        source_ticket = str(event.source_ticket)
        existing = mapping.get(source_ticket)
        if existing and existing.get("status") == "placed":
            destination_ticket = str(existing.get("destination_ticket", ""))
            destination = self._destination_order_row(destination_ticket)
            if destination is None:
                self.logger.error(
                    "ORDER_CREATED skipped because mapped destination is missing. Manual review required source_ticket=%s destination_ticket=%s",
                    source_ticket,
                    destination_ticket,
                )
                return True

            mismatches = _order_field_mismatches(event.current, destination)
            if mismatches:
                self.logger.error(
                    "ORDER_CREATED skipped because mapped destination does not match source. Manual review required source_ticket=%s destination_ticket=%s mismatches=%s",
                    source_ticket,
                    destination_ticket,
                    mismatches,
                )
                return True

            self.logger.info(
                "ORDER_CREATED skipped because source is already mapped source_ticket=%s destination_ticket=%s",
                source_ticket,
                destination_ticket,
            )
            return True

        destination = self._find_existing_equivalent_destination_order(event.current, mapping, source_ticket)
        if destination is None:
            return False

        destination_ticket = str(destination.get("ticket", ""))
        upsert_mapping(
            self.mapping_file,
            {
                "source_ticket": source_ticket,
                "destination_ticket": destination_ticket,
                "symbol": event.current.get("symbol", ""),
                "type": event.current.get("type", ""),
                "source_volume": event.current.get("volume_current", event.current.get("volume_initial", "")),
                "destination_volume": destination.get("volume_current", destination.get("volume_initial", "")),
                "status": "placed",
            },
        )
        self.logger.info(
            "ORDER_CREATED adopted existing destination order instead of opening duplicate source_ticket=%s destination_ticket=%s",
            source_ticket,
            destination_ticket,
        )
        return True

    def _prepare_market_position(self, event: ChangeEvent) -> None:
        if not event.current:
            self.logger.warning("POSITION_OPENED event has no current row: %s", event.to_dict())
            return

        triggered_destination_ticket = self._adopt_triggered_pending_position(event)
        if triggered_destination_ticket is not None:
            self.logger.info(
                "POSITION_OPENED adopted triggered pending order source_ticket=%s destination_ticket=%s",
                event.source_ticket,
                triggered_destination_ticket,
            )
            return

        before_tickets = self._destination_position_tickets()
        prepared = self.gui.prepare_market_position(event.current)
        time.sleep(self.gui.config.order_window_delay_seconds)
        destination_ticket = self._find_new_destination_position_ticket(event.current, before_tickets)

        if self.mapping_file is not None and destination_ticket is not None:
            upsert_mapping(
                self.mapping_file,
                {
                    "source_ticket": event.source_ticket,
                    "destination_ticket": destination_ticket,
                    "symbol": event.current.get("symbol", ""),
                    "type": event.current.get("type", ""),
                    "source_volume": event.current.get("volume", ""),
                    "destination_volume": event.current.get("volume", ""),
                    "status": "placed",
                },
            )
        elif self.mapping_file is not None:
            self.logger.error(
                "POSITION_OPENED executed but destination ticket was not verified source_ticket=%s",
                event.source_ticket,
            )

        self.logger.info(
            "POSITION_OPENED executed source_ticket=%s destination_ticket=%s symbol=%s type=%s volume=%s sl=%s tp=%s screenshot=%s",
            prepared["source_ticket"],
            destination_ticket or "",
            prepared["symbol"],
            prepared["type"],
            prepared["volume"],
            prepared["sl"],
            prepared["tp"],
            prepared["screenshot_order_window"],
        )

    def _close_position(self, event: ChangeEvent) -> None:
        if self.mapping_file is None:
            self.logger.warning(
                "POSITION_CLOSED cannot execute because mapping_file is not configured source_ticket=%s",
                event.source_ticket,
            )
            return

        mapping = load_mapping(self.mapping_file)
        map_row = mapping.get(str(event.source_ticket))
        if not map_row:
            self.logger.warning("POSITION_CLOSED has no destination mapping source_ticket=%s", event.source_ticket)
            return
        if map_row.get("status") != "placed":
            self.logger.info(
                "POSITION_CLOSED skipped because mapping status is not placed source_ticket=%s status=%s",
                event.source_ticket,
                map_row.get("status", ""),
            )
            return

        destination_ticket = str(map_row.get("destination_ticket", ""))
        if not destination_ticket:
            self.logger.warning("POSITION_CLOSED mapping has no destination ticket source_ticket=%s", event.source_ticket)
            return

        current_tickets = self._destination_position_tickets()
        if destination_ticket not in current_tickets:
            updated = dict(map_row)
            updated["status"] = "closed"
            upsert_mapping(self.mapping_file, updated)
            self.logger.info(
                "POSITION_CLOSED mapping marked closed because destination ticket is already absent source_ticket=%s destination_ticket=%s",
                event.source_ticket,
                destination_ticket,
            )
            return

        row_center = self._visible_position_row_center(destination_ticket)
        if row_center is None:
            self.logger.error(
                "POSITION_CLOSED refused: exact destination position row is not visible/current source_ticket=%s destination_ticket=%s",
                event.source_ticket,
                destination_ticket,
            )
            return

        screenshot = self.gui.close_position(
            destination_ticket,
            row_center=row_center,
            trade_type=map_row.get("type", ""),
        )
        time.sleep(self.gui.config.order_window_delay_seconds)
        if destination_ticket in self._destination_position_tickets():
            fallback = getattr(self.gui, "close_position_from_context_menu", None)
            if callable(fallback):
                self.logger.warning(
                    "POSITION_CLOSED primary close did not remove destination_ticket=%s; trying context menu close.",
                    destination_ticket,
                )
                screenshot = fallback(
                    destination_ticket,
                    row_center=row_center,
                    trade_type=map_row.get("type", ""),
                )
                time.sleep(self.gui.config.order_window_delay_seconds)

            if destination_ticket in self._destination_position_tickets():
                self.logger.error(
                    "POSITION_CLOSED did not remove destination_ticket=%s source_ticket=%s screenshot=%s",
                    destination_ticket,
                    event.source_ticket,
                    screenshot,
                )
                return

        updated = dict(map_row)
        updated["status"] = "closed"
        upsert_mapping(self.mapping_file, updated)
        self.logger.info(
            "POSITION_CLOSED executed source_ticket=%s destination_ticket=%s screenshot=%s",
            event.source_ticket,
            destination_ticket,
            screenshot,
        )

    def _delete_order(self, event: ChangeEvent) -> None:
        if _is_market_trade_type(event.trade_type):
            self.logger.info(
                "ORDER_DELETED ignored because it is a transient market row source_ticket=%s type=%s",
                event.source_ticket,
                event.trade_type,
            )
            return
        if self.mapping_file is None:
            self.logger.warning(
                "ORDER_DELETED cannot execute because mapping_file is not configured source_ticket=%s",
                event.source_ticket,
            )
            return

        mapping = load_mapping(self.mapping_file)
        map_row = mapping.get(str(event.source_ticket))
        if not map_row:
            self.logger.warning(
                "ORDER_DELETED has no destination mapping source_ticket=%s",
                event.source_ticket,
            )
            return

        if map_row.get("status") != "placed":
            self.logger.info(
                "ORDER_DELETED ignored because mapping status is %s source_ticket=%s destination_ticket=%s",
                map_row.get("status", ""),
                event.source_ticket,
                map_row.get("destination_ticket", ""),
            )
            return

        destination_ticket = str(map_row.get("destination_ticket", ""))
        row_center = self._visible_order_row_center(destination_ticket)
        if row_center is None:
            self.logger.warning(
                "ORDER_DELETED skipped because destination ticket is not visible/current source_ticket=%s destination_ticket=%s",
                event.source_ticket,
                destination_ticket,
            )
            return

        destination_row = self._destination_order_row(destination_ticket)
        if destination_row is None:
            self.logger.warning(
                "ORDER_DELETED skipped because destination row is missing source_ticket=%s destination_ticket=%s",
                event.source_ticket,
                destination_ticket,
            )
            return

        active_source = self._find_active_source_order_matching_destination(
            destination_row,
            exclude_source_ticket=str(event.source_ticket),
        )
        if active_source is not None:
            active_source_ticket = str(active_source.get("ticket", ""))
            upsert_mapping(
                self.mapping_file,
                {
                    "source_ticket": active_source_ticket,
                    "destination_ticket": destination_ticket,
                    "symbol": active_source.get("symbol", ""),
                    "type": active_source.get("type", ""),
                    "source_volume": active_source.get("volume_current", active_source.get("volume_initial", "")),
                    "destination_volume": destination_row.get(
                        "volume_current",
                        destination_row.get("volume_initial", ""),
                    ),
                    "status": "placed",
                },
            )
            self.logger.warning(
                "ORDER_DELETED skipped because destination still matches active source source_ticket=%s active_source_ticket=%s destination_ticket=%s",
                event.source_ticket,
                active_source_ticket,
                destination_ticket,
            )
            return

        source_row = event.previous or {}
        mismatches = _order_field_mismatches(source_row, destination_row)
        if mismatches:
            self.logger.warning(
                "ORDER_DELETED skipped because mapped destination does not match deleted source source_ticket=%s destination_ticket=%s mismatches=%s",
                event.source_ticket,
                destination_ticket,
                mismatches,
            )
            return

        screenshot = self.gui.delete_pending_order(destination_ticket, row_center=row_center)
        updated = dict(map_row)
        updated["status"] = "canceled"
        upsert_mapping(self.mapping_file, updated)
        self.logger.info(
            "ORDER_DELETED executed source_ticket=%s destination_ticket=%s screenshot=%s",
            event.source_ticket,
            destination_ticket,
            screenshot,
        )

    def _destination_order_row(self, destination_ticket: str) -> dict[str, Any] | None:
        if self.destination_orders_file is None:
            return None
        for row in read_csv_rows(self.destination_orders_file):
            if str(row.get("ticket", "")) == destination_ticket:
                return row
        return None

    def _destination_order_rows(self) -> list[dict[str, Any]]:
        if self.destination_orders_file is None:
            return []
        return read_csv_rows(self.destination_orders_file)

    def _destination_order_tickets(self) -> set[str]:
        if self.destination_orders_file is None:
            return set()
        return {str(row.get("ticket", "")) for row in read_csv_rows(self.destination_orders_file)}

    def _find_existing_equivalent_destination_order(
        self,
        source_order: dict[str, Any],
        mapping: dict[str, dict[str, Any]],
        source_ticket: str,
    ) -> dict[str, Any] | None:
        active_source_tickets = self._active_source_order_tickets()
        mapped_destinations = {
            str(row.get("destination_ticket", ""))
            for mapped_source, row in mapping.items()
            if row.get("status") == "placed"
            and str(mapped_source) != source_ticket
            and (active_source_tickets is None or str(mapped_source) in active_source_tickets)
        }

        for destination in self._destination_order_rows():
            destination_ticket = str(destination.get("ticket", ""))
            if destination_ticket in mapped_destinations:
                continue
            if not _order_field_mismatches(source_order, destination):
                return destination
        return None

    def _find_active_source_order_matching_destination(
        self,
        destination_order: dict[str, Any],
        exclude_source_ticket: str,
    ) -> dict[str, Any] | None:
        if self.source_orders_file is None:
            return None
        for source in read_csv_rows(self.source_orders_file):
            source_ticket = str(source.get("ticket", ""))
            if source_ticket == exclude_source_ticket:
                continue
            if _is_market_trade_type(source.get("type")):
                continue
            if not _order_field_mismatches(source, destination_order):
                return source
        return None

    def _active_source_order_tickets(self) -> set[str] | None:
        if self.source_orders_file is None:
            return None
        return {
            str(row.get("ticket", ""))
            for row in read_csv_rows(self.source_orders_file)
            if not _is_market_trade_type(row.get("type"))
        }

    def _log_unsupported_action(self, event: ChangeEvent) -> None:
        try:
            focused = self.gui.focus_mt5()
            screenshot_path = self.gui.screenshot(event.change_type.value)
            self.logger.info(
                "GUI READY focused=%s event=%s source_ticket=%s screenshot=%s",
                focused,
                event.change_type.value,
                event.source_ticket,
                screenshot_path,
            )
        except GuiSafetyError:
            raise
        except Exception:
            self.logger.exception("GUI preparation failed for event: %s", event.to_dict())

    def _visible_order_row_center(self, destination_ticket: str) -> tuple[int, int] | None:
        if self.destination_orders_file is None:
            return None

        rows = read_csv_rows(self.destination_orders_file)
        tickets = [str(row.get("ticket", "")) for row in rows]
        if destination_ticket not in tickets:
            return None

        index = tickets.index(destination_ticket)
        coordinates = self.gui.config.order_form_coordinates
        anchor_x, top_y = coordinates.get("order_row_anchor", (253, 741))
        _, step_y = coordinates.get("order_row_step_y", (0, 20))
        y = top_y + (index * step_y)
        return anchor_x, y

    def _destination_position_tickets(self) -> set[str]:
        if self.destination_positions_file is None:
            return set()
        return {str(row.get("ticket", "")) for row in read_csv_rows(self.destination_positions_file)}

    def _visible_position_row_center(self, destination_ticket: str) -> tuple[int, int] | None:
        if self.destination_positions_file is None:
            return None
        rows = read_csv_rows(self.destination_positions_file)
        tickets = [str(row.get("ticket", "")) for row in rows]
        if destination_ticket not in tickets:
            return None
        index = tickets.index(destination_ticket)
        coordinates = self.gui.config.order_form_coordinates
        anchor_x, top_y = coordinates.get(
            "position_row_anchor",
            coordinates.get("position_row_fallback", (253, 721)),
        )
        _, step_y = coordinates.get("position_row_step_y", coordinates.get("order_row_step_y", (0, 20)))
        y = top_y + (index * step_y)
        max_y = coordinates.get("position_row_max_y", coordinates.get("order_row_max_y", (0, 941)))[1]
        if y > max_y:
            return None
        return anchor_x, y

    def _find_new_destination_position_ticket(
        self,
        source_position: dict[str, Any],
        before_tickets: set[str],
    ) -> str | None:
        if self.destination_positions_file is None:
            return None

        for row in read_csv_rows(self.destination_positions_file):
            ticket = str(row.get("ticket", ""))
            if ticket in before_tickets:
                continue
            if _position_matches_market_source(source_position, row):
                return ticket
        return None

    def _find_new_destination_order_ticket(
        self,
        source_order: dict[str, Any],
        before_tickets: set[str],
    ) -> str | None:
        if self.destination_orders_file is None:
            return None

        for row in read_csv_rows(self.destination_orders_file):
            ticket = str(row.get("ticket", ""))
            if ticket in before_tickets:
                continue
            if not _order_field_mismatches(source_order, row):
                return ticket
        return None

    def _adopt_triggered_pending_position(self, event: ChangeEvent) -> str | None:
        if self.mapping_file is None or self.destination_orders_file is None or self.destination_positions_file is None:
            return None

        source_position = event.current or {}
        mapping = load_mapping(self.mapping_file)
        active_destination_positions = self._destination_position_rows()
        active_destination_orders = self._destination_order_tickets()
        used_destination_positions = {
            str(row.get("destination_ticket", ""))
            for row in mapping.values()
            if row.get("status") == "placed" and _is_market_trade_type(row.get("type"))
        }

        for source_order_ticket, map_row in mapping.items():
            if map_row.get("status") != "placed":
                continue
            if _is_market_trade_type(map_row.get("type")):
                continue

            destination_order_ticket = str(map_row.get("destination_ticket", ""))
            if not destination_order_ticket or destination_order_ticket in active_destination_orders:
                continue
            if _pending_type_to_market_type(map_row.get("type")) != _normalize_compare_value(source_position.get("type")):
                continue
            if _normalize_compare_value(map_row.get("symbol")) != _normalize_compare_value(source_position.get("symbol")):
                continue
            if _normalize_compare_value(map_row.get("source_volume")) != _normalize_compare_value(source_position.get("volume")):
                continue

            destination_ticket = self._find_matching_unmapped_destination_position(
                source_position,
                active_destination_positions,
                used_destination_positions,
            )
            if destination_ticket is None:
                continue

            pending_update = dict(map_row)
            pending_update["status"] = "triggered"
            upsert_mapping(self.mapping_file, pending_update)
            upsert_mapping(
                self.mapping_file,
                {
                    "source_ticket": event.source_ticket,
                    "destination_ticket": destination_ticket,
                    "symbol": source_position.get("symbol", ""),
                    "type": source_position.get("type", ""),
                    "source_volume": source_position.get("volume", ""),
                    "destination_volume": source_position.get("volume", ""),
                    "status": "placed",
                },
            )
            self.logger.info(
                "Mapped triggered pending source_order=%s destination_order=%s source_position=%s destination_position=%s",
                source_order_ticket,
                destination_order_ticket,
                event.source_ticket,
                destination_ticket,
            )
            return destination_ticket

        return None

    def _destination_position_rows(self) -> list[dict[str, Any]]:
        if self.destination_positions_file is None:
            return []
        return read_csv_rows(self.destination_positions_file)

    def _find_matching_unmapped_destination_position(
        self,
        source_position: dict[str, Any],
        destination_rows: list[dict[str, Any]],
        used_destination_positions: set[str],
    ) -> str | None:
        for row in destination_rows:
            ticket = str(row.get("ticket", ""))
            if ticket in used_destination_positions:
                continue
            if _position_matches_market_source(source_position, row):
                return ticket
        return None


def _order_field_mismatches(
    source_row: dict[str, Any],
    destination_row: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    fields = ("symbol", "type", "price_open", "sl", "tp")
    mismatches: dict[str, dict[str, Any]] = {}

    for field in fields:
        source_value = _normalize_compare_value(source_row.get(field))
        destination_value = _normalize_compare_value(destination_row.get(field))
        if source_value != destination_value:
            mismatches[field] = {
                "source": source_row.get(field),
                "destination": destination_row.get(field),
            }

    source_volume = _normalize_compare_value(
        source_row.get("volume_current", source_row.get("volume_initial", ""))
    )
    destination_volume = _normalize_compare_value(
        destination_row.get("volume_current", destination_row.get("volume_initial", ""))
    )
    if source_volume != destination_volume:
        mismatches["volume"] = {
            "source": source_row.get("volume_current", source_row.get("volume_initial", "")),
            "destination": destination_row.get("volume_current", destination_row.get("volume_initial", "")),
        }

    return mismatches


def _normalize_compare_value(value: Any) -> str:
    if value in {None, ""}:
        return ""
    try:
        return f"{float(value):.5f}"
    except (TypeError, ValueError):
        return str(value).strip().upper()


def _position_matches_market_source(
    source_position: dict[str, Any],
    destination_position: dict[str, Any],
) -> bool:
    return (
        _normalize_compare_value(source_position.get("symbol"))
        == _normalize_compare_value(destination_position.get("symbol"))
        and _normalize_compare_value(source_position.get("type"))
        == _normalize_compare_value(destination_position.get("type"))
        and _normalize_compare_value(source_position.get("volume"))
        == _normalize_compare_value(destination_position.get("volume"))
    )


def _is_market_trade_type(trade_type: Any) -> bool:
    return str(trade_type).strip().upper() in {"BUY", "SELL"}


def _pending_type_to_market_type(trade_type: Any) -> str:
    text = str(trade_type).strip().upper()
    if text.startswith("BUY_"):
        return "BUY"
    if text.startswith("SELL_"):
        return "SELL"
    return text


def gui_config_from_executor_settings(settings: dict[str, Any], project_root: Path) -> GuiConfig:
    def project_path(value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return project_root / path

    return GuiConfig(
        window_title_contains=str(settings.get("mt5_window_title_contains", "MetaTrader")),
        screenshot_dir=project_path(str(settings.get("screenshot_dir", "data/screenshots"))),
        image_dir=project_path(str(settings.get("image_dir", "data/images"))),
        image_confidence=float(settings.get("image_confidence", 0.85)),
        action_pause_seconds=float(settings.get("action_pause_seconds", 0.15)),
        fail_safe=bool(settings.get("fail_safe", True)),
        armed_for_trading=bool(settings.get("armed_for_trading", False)),
        submit_orders=bool(settings.get("submit_orders", False)),
        new_order_hotkey=tuple(settings.get("new_order_hotkey", ["f9"])),
        new_order_button=(
            tuple(int(part) for part in settings["new_order_button"])
            if "new_order_button" in settings
            else None
        ),
        order_dialog_title_contains=str(settings.get("order_dialog_title_contains", "Orden")),
        order_window_delay_seconds=float(settings.get("order_window_delay_seconds", 1.0)),
        field_delay_seconds=float(settings.get("field_delay_seconds", 0.25)),
        comment_prefix=str(settings.get("comment_prefix", "COPY_")),
        order_form_coordinates={
            key: (int(value[0]), int(value[1]))
            for key, value in dict(settings.get("order_form_coordinates", {})).items()
        },
        order_search_scroll_pages=int(settings.get("order_search_scroll_pages", 4)),
        order_search_scroll_clicks=int(settings.get("order_search_scroll_clicks", 8)),
        order_search_arrow_down_presses=int(settings.get("order_search_arrow_down_presses", 8)),
    )


def build_gui_controller(
    executor_settings: dict[str, Any],
    project_root: Path,
    logger: logging.Logger,
) -> Mt5GuiController:
    return Mt5GuiController(
        gui_config_from_executor_settings(executor_settings, project_root),
        logger,
    )


def build_executor(
    mode: str,
    pyautogui_enabled: bool,
    logger: logging.Logger,
    executor_settings: dict[str, Any] | None = None,
    project_root: Path | None = None,
    mapping_file: Path | None = None,
    destination_orders_file: Path | None = None,
    destination_positions_file: Path | None = None,
    source_orders_file: Path | None = None,
):
    if mode == "pyautogui" and pyautogui_enabled:
        if executor_settings is None or project_root is None:
            raise ValueError("executor_settings and project_root are required for pyautogui mode")
        gui = build_gui_controller(executor_settings, project_root, logger)
        return PyAutoGuiExecutor(
            gui,
            logger,
            mapping_file=mapping_file,
            source_orders_file=source_orders_file,
            destination_orders_file=destination_orders_file,
            destination_positions_file=destination_positions_file,
        )
    return DryRunExecutor(logger)
