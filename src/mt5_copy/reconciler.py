from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .csv_reader import read_csv_rows, rows_to_snapshot
from .mapping import load_mapping, upsert_mapping
from .mt5_gui import Mt5GuiController


@dataclass(frozen=True)
class ReconcileIssue:
    source_ticket: str
    destination_ticket: str
    issue_type: str
    field_diffs: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class AuthoritySyncReport:
    exact_mapped: int
    created: int
    deleted: int
    missing_sources: list[str]
    extra_destinations: list[str]
    skipped: list[str]


@dataclass(frozen=True)
class PositionSyncReport:
    created: int
    deleted: int
    missing_sources: list[str]
    extra_destinations: list[str]
    skipped: list[str]


def find_order_discrepancies(
    source_orders: dict[str, dict[str, Any]],
    destination_orders: dict[str, dict[str, Any]],
    mapping: dict[str, dict[str, Any]],
) -> list[ReconcileIssue]:
    return _find_sl_tp_discrepancies(
        source_rows={ticket: row for ticket, row in source_orders.items() if not _is_market_type(row.get("type"))},
        destination_rows={
            ticket: row for ticket, row in destination_orders.items() if not _is_market_type(row.get("type"))
        },
        mapping=mapping,
        missing_prefix="order",
    )


def reconcile_orders_to_source_authority(
    source_orders_file: Path,
    destination_orders_file: Path,
    mapping_file: Path,
    gui: Mt5GuiController,
    logger: logging.Logger,
    verify_delay_seconds: float = 1.5,
    before_delete_check=None,
) -> AuthoritySyncReport:
    source_rows = _pending_order_rows(read_csv_rows(source_orders_file))
    destination_rows = _pending_order_rows(read_csv_rows(destination_orders_file))
    source_by_ticket = {str(row.get("ticket", "")): row for row in source_rows}
    destination_by_ticket = {str(row.get("ticket", "")): row for row in destination_rows}
    mapping = load_mapping(mapping_file)

    exact_mapped = _map_exact_matches(source_rows, destination_rows, mapping_file, mapping, logger)

    source_counts = _signature_counts(source_rows)
    destination_counts = _signature_counts(destination_rows)
    missing_sources = _surplus_source_tickets(source_rows, source_counts, destination_counts)
    mapping = load_mapping(mapping_file)
    extra_destinations = _surplus_destination_tickets(
        destination_rows,
        source_counts,
        destination_counts,
        mapping,
    )

    created = 0
    deleted = 0
    skipped: list[str] = []

    for source_ticket in missing_sources:
        source = source_by_ticket[source_ticket]
        if not gui.config.submit_orders:
            skipped.append(f"create:{source_ticket}:submit_orders_disabled")
            logger.warning(
                "AUTHORITY_SYNC create skipped source_ticket=%s because submit_orders=false",
                source_ticket,
            )
            continue

        before_tickets = {
            str(row.get("ticket", ""))
            for row in _pending_order_rows(read_csv_rows(destination_orders_file))
        }
        logger.info("AUTHORITY_SYNC creating missing order source_ticket=%s", source_ticket)
        gui.prepare_pending_order(source)
        time.sleep(verify_delay_seconds)
        refreshed = _pending_order_rows(read_csv_rows(destination_orders_file))
        created_ticket = _find_new_matching_destination_ticket(source, refreshed, before_tickets)
        if created_ticket is None:
            skipped.append(f"create:{source_ticket}:destination_ticket_not_found")
            logger.error("AUTHORITY_SYNC create could not verify destination ticket source_ticket=%s", source_ticket)
            continue

        destination = {str(row.get("ticket", "")): row for row in refreshed}[created_ticket]
        upsert_mapping(
            mapping_file,
            {
                "source_ticket": source_ticket,
                "destination_ticket": created_ticket,
                "symbol": source.get("symbol", ""),
                "type": source.get("type", ""),
                "source_volume": source.get("volume_current", source.get("volume_initial", "")),
                "destination_volume": destination.get("volume_current", destination.get("volume_initial", "")),
                "status": "placed",
            },
        )
        created += 1
        logger.info(
            "AUTHORITY_SYNC created source_ticket=%s destination_ticket=%s",
            source_ticket,
            created_ticket,
        )

    for destination_ticket in extra_destinations:
        refreshed = _pending_order_rows(read_csv_rows(destination_orders_file))
        refreshed_destinations = {str(row.get("ticket", "")): row for row in refreshed}
        destination = refreshed_destinations.get(destination_ticket)
        if destination is None:
            continue

        if before_delete_check is not None:
            before_delete_check(destination_ticket)

        if not _destination_order_is_still_extra(source_orders_file, destination_orders_file, destination):
            source_ticket = _source_ticket_for_signature(source_orders_file, destination, mapping_file)
            if source_ticket:
                upsert_mapping(
                    mapping_file,
                    {
                        "source_ticket": source_ticket,
                        "destination_ticket": destination_ticket,
                        "symbol": destination.get("symbol", ""),
                        "type": destination.get("type", ""),
                        "source_volume": destination.get("volume_current", destination.get("volume_initial", "")),
                        "destination_volume": destination.get("volume_current", destination.get("volume_initial", "")),
                        "status": "placed",
                    },
                )
            skipped.append(f"delete:{destination_ticket}:now_matches_source")
            logger.info(
                "AUTHORITY_SYNC delete skipped destination now matches source destination_ticket=%s source_ticket=%s",
                destination_ticket,
                source_ticket or "",
            )
            continue

        row_center = _row_center_for_destination_ticket(destination_ticket, refreshed, gui)
        logger.info("AUTHORITY_SYNC deleting extra destination_ticket=%s", destination_ticket)
        gui.delete_pending_order(destination_ticket, row_center=row_center)
        time.sleep(verify_delay_seconds)
        if destination_ticket in {
            str(row.get("ticket", ""))
            for row in _pending_order_rows(read_csv_rows(destination_orders_file))
        }:
            skipped.append(f"delete:{destination_ticket}:still_present")
            logger.error("AUTHORITY_SYNC delete did not remove destination_ticket=%s", destination_ticket)
            continue

        _mark_destination_canceled(mapping_file, destination_ticket)
        deleted += 1
        logger.info("AUTHORITY_SYNC deleted destination_ticket=%s", destination_ticket)

    return AuthoritySyncReport(
        exact_mapped=exact_mapped,
        created=created,
        deleted=deleted,
        missing_sources=missing_sources,
        extra_destinations=extra_destinations,
        skipped=skipped,
    )


def reconcile_positions_to_source_authority(
    source_positions_file: Path,
    destination_positions_file: Path,
    mapping_file: Path,
    gui: Mt5GuiController,
    logger: logging.Logger,
    verify_delay_seconds: float = 1.5,
) -> PositionSyncReport:
    source_rows = read_csv_rows(source_positions_file)
    destination_rows = read_csv_rows(destination_positions_file)
    mapping = load_mapping(mapping_file)
    missing_sources = _surplus_position_source_tickets(source_rows, destination_rows)
    extra_destinations = _surplus_position_destination_tickets(source_rows, destination_rows, mapping)
    before_tickets = {str(row.get("ticket", "")) for row in destination_rows}
    created = 0
    deleted = 0
    skipped: list[str] = []

    for source_ticket in missing_sources:
        existing_map = mapping.get(str(source_ticket))
        if existing_map and existing_map.get("status") == "placed":
            skipped.append(f"create_position:{source_ticket}:mapped_destination_missing_manual_review")
            logger.error(
                "AUTHORITY_SYNC position create blocked because mapped destination is missing. "
                "Manual review required source_ticket=%s destination_ticket=%s",
                source_ticket,
                existing_map.get("destination_ticket", ""),
            )
            continue

        source = next(row for row in source_rows if str(row.get("ticket", "")) == source_ticket)
        if not gui.config.submit_orders:
            skipped.append(f"create_position:{source_ticket}:submit_orders_disabled")
            logger.warning(
                "AUTHORITY_SYNC position create skipped source_ticket=%s because submit_orders=false",
                source_ticket,
            )
            continue

        logger.info("AUTHORITY_SYNC creating missing market position source_ticket=%s", source_ticket)
        gui.prepare_market_position(source)
        time.sleep(verify_delay_seconds)
        refreshed = read_csv_rows(destination_positions_file)
        destination_ticket = _find_new_matching_position_ticket(source, refreshed, before_tickets)
        if destination_ticket is None:
            skipped.append(f"create_position:{source_ticket}:destination_ticket_not_found")
            logger.error(
                "AUTHORITY_SYNC position create could not verify destination ticket source_ticket=%s",
                source_ticket,
            )
            continue

        destination = {str(row.get("ticket", "")): row for row in refreshed}[destination_ticket]
        upsert_mapping(
            mapping_file,
            {
                "source_ticket": source_ticket,
                "destination_ticket": destination_ticket,
                "symbol": source.get("symbol", ""),
                "type": source.get("type", ""),
                "source_volume": source.get("volume", ""),
                "destination_volume": destination.get("volume", ""),
                "status": "placed",
            },
        )
        before_tickets.add(destination_ticket)
        created += 1
        logger.info(
            "AUTHORITY_SYNC created market position source_ticket=%s destination_ticket=%s",
            source_ticket,
            destination_ticket,
        )

    for destination_ticket in extra_destinations:
        refreshed = read_csv_rows(destination_positions_file)
        refreshed_destinations = {str(row.get("ticket", "")): row for row in refreshed}
        destination = refreshed_destinations.get(destination_ticket)
        if destination is None:
            continue

        if not _destination_position_is_still_extra(source_positions_file, destination_positions_file, destination):
            source_ticket = _source_position_ticket_for_signature(source_positions_file, destination, mapping_file)
            if source_ticket:
                upsert_mapping(
                    mapping_file,
                    {
                        "source_ticket": source_ticket,
                        "destination_ticket": destination_ticket,
                        "symbol": destination.get("symbol", ""),
                        "type": destination.get("type", ""),
                        "source_volume": destination.get("volume", ""),
                        "destination_volume": destination.get("volume", ""),
                        "status": "placed",
                    },
                )
            skipped.append(f"close_position:{destination_ticket}:now_matches_source")
            logger.info(
                "AUTHORITY_SYNC position close skipped destination now matches source destination_ticket=%s source_ticket=%s",
                destination_ticket,
                source_ticket or "",
            )
            continue

        if not gui.config.submit_orders:
            skipped.append(f"close_position:{destination_ticket}:submit_orders_disabled")
            logger.warning(
                "AUTHORITY_SYNC position close skipped destination_ticket=%s because submit_orders=false",
                destination_ticket,
            )
            continue

        row_center = _row_center_for_position_ticket(destination_ticket, refreshed, gui)
        if row_center is None:
            skipped.append(f"close_position:{destination_ticket}:row_not_visible")
            logger.error(
                "AUTHORITY_SYNC position close refused because row is not visible/current destination_ticket=%s",
                destination_ticket,
            )
            continue

        logger.info("AUTHORITY_SYNC closing extra market position destination_ticket=%s", destination_ticket)
        gui.close_position(
            destination_ticket,
            row_center=row_center,
            trade_type=str(destination.get("type", "")),
        )
        time.sleep(verify_delay_seconds)
        if destination_ticket in {str(row.get("ticket", "")) for row in read_csv_rows(destination_positions_file)}:
            skipped.append(f"close_position:{destination_ticket}:still_present")
            logger.error("AUTHORITY_SYNC position close did not remove destination_ticket=%s", destination_ticket)
            continue

        _mark_destination_closed(mapping_file, destination_ticket)
        deleted += 1
        logger.info("AUTHORITY_SYNC closed extra market position destination_ticket=%s", destination_ticket)

    return PositionSyncReport(
        created=created,
        deleted=deleted,
        missing_sources=missing_sources,
        extra_destinations=extra_destinations,
        skipped=skipped,
    )


def find_position_discrepancies(
    source_positions: dict[str, dict[str, Any]],
    destination_positions: dict[str, dict[str, Any]],
    mapping: dict[str, dict[str, Any]],
) -> list[ReconcileIssue]:
    return _find_sl_tp_discrepancies(
        source_rows=source_positions,
        destination_rows=destination_positions,
        mapping=mapping,
        missing_prefix="position",
    )


def _find_sl_tp_discrepancies(
    source_rows: dict[str, dict[str, Any]],
    destination_rows: dict[str, dict[str, Any]],
    mapping: dict[str, dict[str, Any]],
    missing_prefix: str,
) -> list[ReconcileIssue]:
    issues: list[ReconcileIssue] = []

    for source_ticket, map_row in mapping.items():
        if map_row.get("status") != "placed":
            continue
        if not _mapping_matches_prefix(map_row, missing_prefix):
            continue

        destination_ticket = str(map_row.get("destination_ticket", ""))
        source = source_rows.get(str(source_ticket))
        destination = destination_rows.get(destination_ticket)

        if source is None:
            issues.append(
                ReconcileIssue(source_ticket, destination_ticket, f"{missing_prefix}_source_missing", {})
            )
            continue

        if destination is None:
            issues.append(
                ReconcileIssue(
                    source_ticket,
                    destination_ticket,
                    f"{missing_prefix}_destination_missing",
                    {},
                )
            )
            continue

        diffs = _field_diffs(source, destination, ("sl", "tp"))
        if diffs:
            issues.append(
                ReconcileIssue(
                    source_ticket,
                    destination_ticket,
                    f"{missing_prefix}_sl_tp_mismatch",
                    diffs,
                )
            )

    return issues


def _mapping_matches_prefix(map_row: dict[str, Any], missing_prefix: str) -> bool:
    trade_type = str(map_row.get("type", "")).strip().upper()
    if missing_prefix == "position":
        return _is_market_type(trade_type)
    if missing_prefix == "order":
        return not _is_market_type(trade_type)
    return True


def _pending_order_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not _is_market_type(row.get("type"))]


def _is_market_type(trade_type: Any) -> bool:
    return str(trade_type).strip().upper() in {"BUY", "SELL"}


def reconcile_sl_tp(
    source_positions_file: Path,
    source_orders_file: Path,
    destination_positions_file: Path,
    destination_orders_file: Path,
    mapping_file: Path,
    gui: Mt5GuiController,
    logger: logging.Logger,
    max_retries: int = 2,
    verify_delay_seconds: float = 1.5,
    issue_scope: str = "all",
) -> list[ReconcileIssue]:
    source_positions = rows_to_snapshot(read_csv_rows(source_positions_file))
    source_orders = rows_to_snapshot(read_csv_rows(source_orders_file))
    destination_positions = rows_to_snapshot(read_csv_rows(destination_positions_file))
    destination_orders = rows_to_snapshot(read_csv_rows(destination_orders_file))
    mapping = load_mapping(mapping_file)
    issues: list[ReconcileIssue] = []
    if issue_scope in {"all", "orders"}:
        issues.extend(find_order_discrepancies(source_orders, destination_orders, mapping))
    if issue_scope in {"all", "positions"}:
        issues.extend(find_position_discrepancies(source_positions, destination_positions, mapping))

    for issue in issues:
        if issue.issue_type not in {"order_sl_tp_mismatch", "position_sl_tp_mismatch"}:
            logger.warning("Reconcile issue needs non-SL/TP action: %s", issue)
            continue

        is_position = issue.issue_type == "position_sl_tp_mismatch"
        source = source_positions[issue.source_ticket] if is_position else source_orders[issue.source_ticket]
        for attempt in range(1, max_retries + 1):
            logger.info(
                "Reconciling SL/TP attempt=%s source=%s destination=%s diffs=%s",
                attempt,
                issue.source_ticket,
                issue.destination_ticket,
                issue.field_diffs,
            )
            if is_position:
                destination_rows = read_csv_rows(destination_positions_file)
                row_center = _row_center_for_position_ticket(issue.destination_ticket, destination_rows, gui)
                if row_center is None:
                    logger.error(
                        "Refusing SL/TP position modify because destination row is not visible/current source=%s destination=%s",
                        issue.source_ticket,
                        issue.destination_ticket,
                    )
                    break
                try:
                    gui.modify_position_sl_tp(
                        issue.destination_ticket,
                        sl=source.get("sl", ""),
                        tp=source.get("tp", ""),
                        row_center=row_center,
                    )
                except Exception:
                    logger.exception(
                        "GUI failed while reconciling position SL/TP source=%s destination=%s",
                        issue.source_ticket,
                        issue.destination_ticket,
                    )
                    _dismiss_gui_dialog(gui, logger)
                    break
            else:
                try:
                    gui.modify_pending_order_sl_tp(
                        issue.destination_ticket,
                        sl=source.get("sl", ""),
                        tp=source.get("tp", ""),
                    )
                except Exception:
                    logger.exception(
                        "GUI failed while reconciling order SL/TP source=%s destination=%s",
                        issue.source_ticket,
                        issue.destination_ticket,
                    )
                    _dismiss_gui_dialog(gui, logger)
                    break
            time.sleep(verify_delay_seconds)

            refreshed_file = destination_positions_file if is_position else destination_orders_file
            refreshed_destination = rows_to_snapshot(read_csv_rows(refreshed_file))
            refreshed = refreshed_destination.get(issue.destination_ticket)
            if refreshed and not _field_diffs(source, refreshed, ("sl", "tp")):
                logger.info(
                    "SL/TP reconciled source=%s destination=%s",
                    issue.source_ticket,
                    issue.destination_ticket,
                )
                break
        else:
            logger.error(
                "Failed to reconcile SL/TP source=%s destination=%s",
                issue.source_ticket,
                issue.destination_ticket,
            )

    refreshed_source_positions = rows_to_snapshot(read_csv_rows(source_positions_file))
    refreshed_source = rows_to_snapshot(read_csv_rows(source_orders_file))
    refreshed_destination_positions = rows_to_snapshot(read_csv_rows(destination_positions_file))
    refreshed_destination = rows_to_snapshot(read_csv_rows(destination_orders_file))
    remaining: list[ReconcileIssue] = []
    if issue_scope in {"all", "orders"}:
        remaining.extend(find_order_discrepancies(refreshed_source, refreshed_destination, mapping))
    if issue_scope in {"all", "positions"}:
        remaining.extend(
            find_position_discrepancies(
                refreshed_source_positions,
                refreshed_destination_positions,
                mapping,
            )
        )
    return remaining


def _field_diffs(
    source: dict[str, Any],
    destination: dict[str, Any],
    fields: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    diffs: dict[str, dict[str, Any]] = {}
    for field in fields:
        source_value = _normalize_number(source.get(field))
        destination_value = _normalize_number(destination.get(field))
        if source_value != destination_value:
            diffs[field] = {
                "source": source.get(field),
                "destination": destination.get(field),
            }
    return diffs


def _dismiss_gui_dialog(gui: Mt5GuiController, logger: logging.Logger) -> None:
    try:
        gui.close_active_dialog()
    except Exception:
        logger.exception("Failed to dismiss GUI dialog after reconciliation error.")


def _map_exact_matches(
    source_rows: list[dict[str, Any]],
    destination_rows: list[dict[str, Any]],
    mapping_file: Path,
    mapping: dict[str, dict[str, Any]],
    logger: logging.Logger,
) -> int:
    mapped = 0
    used_destinations = {
        str(row.get("destination_ticket", ""))
        for row in mapping.values()
        if row.get("status") == "placed"
    }

    destination_by_signature: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for destination in destination_rows:
        destination_by_signature.setdefault(_order_signature(destination), []).append(destination)

    for source in source_rows:
        source_ticket = str(source.get("ticket", ""))
        current = mapping.get(source_ticket)
        if current and current.get("status") == "placed":
            continue

        candidates = destination_by_signature.get(_order_signature(source), [])
        destination = next(
            (
                candidate
                for candidate in candidates
                if str(candidate.get("ticket", "")) not in used_destinations
            ),
            None,
        )
        if destination is None:
            continue

        destination_ticket = str(destination.get("ticket", ""))
        upsert_mapping(
            mapping_file,
            {
                "source_ticket": source_ticket,
                "destination_ticket": destination_ticket,
                "symbol": source.get("symbol", ""),
                "type": source.get("type", ""),
                "source_volume": source.get("volume_current", source.get("volume_initial", "")),
                "destination_volume": destination.get("volume_current", destination.get("volume_initial", "")),
                "status": "placed",
            },
        )
        used_destinations.add(destination_ticket)
        mapped += 1
        logger.info(
            "AUTHORITY_SYNC mapped exact source_ticket=%s destination_ticket=%s",
            source_ticket,
            destination_ticket,
        )

    return mapped


def _signature_counts(rows: list[dict[str, Any]]) -> dict[tuple[str, ...], int]:
    counts: dict[tuple[str, ...], int] = {}
    for row in rows:
        signature = _order_signature(row)
        counts[signature] = counts.get(signature, 0) + 1
    return counts


def _surplus_source_tickets(
    source_rows: list[dict[str, Any]],
    source_counts: dict[tuple[str, ...], int],
    destination_counts: dict[tuple[str, ...], int],
) -> list[str]:
    remaining_needed = {
        signature: max(0, count - destination_counts.get(signature, 0))
        for signature, count in source_counts.items()
    }
    missing: list[str] = []
    for row in source_rows:
        signature = _order_signature(row)
        if remaining_needed.get(signature, 0) <= 0:
            continue
        missing.append(str(row.get("ticket", "")))
        remaining_needed[signature] -= 1
    return missing


def _surplus_destination_tickets(
    destination_rows: list[dict[str, Any]],
    source_counts: dict[tuple[str, ...], int],
    destination_counts: dict[tuple[str, ...], int],
    mapping: dict[str, dict[str, Any]],
) -> list[str]:
    mapped_destinations = {
        str(row.get("destination_ticket", ""))
        for row in mapping.values()
        if row.get("status") == "placed"
    }
    extra: list[str] = []

    for signature, destination_count in destination_counts.items():
        source_count = source_counts.get(signature, 0)
        if destination_count <= source_count:
            continue

        rows = [row for row in destination_rows if _order_signature(row) == signature]
        keep: set[str] = set()

        for row in rows:
            ticket = str(row.get("ticket", ""))
            if ticket not in mapped_destinations:
                continue
            if len(keep) >= source_count:
                break
            keep.add(ticket)

        for row in rows:
            ticket = str(row.get("ticket", ""))
            if len(keep) >= source_count:
                break
            keep.add(ticket)

        extra.extend(str(row.get("ticket", "")) for row in rows if str(row.get("ticket", "")) not in keep)

    return extra


def _find_new_matching_destination_ticket(
    source: dict[str, Any],
    destination_rows: list[dict[str, Any]],
    before_tickets: set[str],
) -> str | None:
    source_signature = _order_signature(source)
    for row in destination_rows:
        ticket = str(row.get("ticket", ""))
        if ticket in before_tickets:
            continue
        if _order_signature(row) == source_signature:
            return ticket
    return None


def _source_ticket_for_signature(
    source_orders_file: Path,
    destination: dict[str, Any],
    mapping_file: Path,
) -> str | None:
    target_signature = _order_signature(destination)
    mapped_sources = {
        str(row.get("source_ticket", ""))
        for row in load_mapping(mapping_file).values()
        if row.get("status") == "placed"
    }
    for source in _pending_order_rows(read_csv_rows(source_orders_file)):
        source_ticket = str(source.get("ticket", ""))
        if source_ticket in mapped_sources:
            continue
        if _order_signature(source) == target_signature:
            return source_ticket
    return None


def _destination_order_is_still_extra(
    source_orders_file: Path,
    destination_orders_file: Path,
    destination: dict[str, Any],
) -> bool:
    signature = _order_signature(destination)
    source_count = sum(
        1 for row in _pending_order_rows(read_csv_rows(source_orders_file)) if _order_signature(row) == signature
    )
    destination_count = sum(
        1
        for row in _pending_order_rows(read_csv_rows(destination_orders_file))
        if _order_signature(row) == signature
    )
    return destination_count > source_count


def _surplus_position_source_tickets(
    source_rows: list[dict[str, Any]],
    destination_rows: list[dict[str, Any]],
) -> list[str]:
    source_counts = _position_signature_counts(source_rows)
    destination_counts = _position_signature_counts(destination_rows)
    remaining_needed = {
        signature: max(0, count - destination_counts.get(signature, 0))
        for signature, count in source_counts.items()
    }
    missing: list[str] = []
    for row in source_rows:
        signature = _position_signature(row)
        if remaining_needed.get(signature, 0) <= 0:
            continue
        missing.append(str(row.get("ticket", "")))
        remaining_needed[signature] -= 1
    return missing


def _surplus_position_destination_tickets(
    source_rows: list[dict[str, Any]],
    destination_rows: list[dict[str, Any]],
    mapping: dict[str, dict[str, Any]],
) -> list[str]:
    source_counts = _position_signature_counts(source_rows)
    destination_counts = _position_signature_counts(destination_rows)
    mapped_destinations = {
        str(row.get("destination_ticket", ""))
        for row in mapping.values()
        if row.get("status") == "placed" and _is_market_type(row.get("type"))
    }
    extra: list[str] = []

    for signature, destination_count in destination_counts.items():
        source_count = source_counts.get(signature, 0)
        if destination_count <= source_count:
            continue

        rows = [row for row in destination_rows if _position_signature(row) == signature]
        keep: set[str] = set()

        for row in rows:
            ticket = str(row.get("ticket", ""))
            if ticket not in mapped_destinations:
                continue
            if len(keep) >= source_count:
                break
            keep.add(ticket)

        for row in rows:
            ticket = str(row.get("ticket", ""))
            if len(keep) >= source_count:
                break
            keep.add(ticket)

        extra.extend(str(row.get("ticket", "")) for row in rows if str(row.get("ticket", "")) not in keep)

    return extra


def _find_new_matching_position_ticket(
    source: dict[str, Any],
    destination_rows: list[dict[str, Any]],
    before_tickets: set[str],
) -> str | None:
    source_signature = _position_signature(source)
    for row in destination_rows:
        ticket = str(row.get("ticket", ""))
        if ticket in before_tickets:
            continue
        if _position_signature(row) == source_signature:
            return ticket
    return None


def _position_signature_counts(rows: list[dict[str, Any]]) -> dict[tuple[str, ...], int]:
    counts: dict[tuple[str, ...], int] = {}
    for row in rows:
        signature = _position_signature(row)
        counts[signature] = counts.get(signature, 0) + 1
    return counts


def _position_signature(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        _normalize_signature_text(row.get("symbol")),
        _normalize_signature_text(row.get("type")),
        _normalize_signature_number(row.get("volume")),
        _normalize_signature_number(row.get("sl")),
        _normalize_signature_number(row.get("tp")),
    )


def _row_center_for_destination_ticket(
    destination_ticket: str,
    destination_rows: list[dict[str, Any]],
    gui: Mt5GuiController,
) -> tuple[int, int] | None:
    tickets = [str(row.get("ticket", "")) for row in destination_rows]
    if destination_ticket not in tickets:
        return None

    index = tickets.index(destination_ticket)
    coordinates = gui.config.order_form_coordinates
    anchor_x, top_y = coordinates.get("order_row_anchor", (253, 741))
    _, step_y = coordinates.get("order_row_step_y", (0, 20))
    y = top_y + (index * step_y)
    max_y = coordinates.get("order_row_max_y", (0, 941))[1]
    if y > max_y:
        return None
    return anchor_x, y


def _row_center_for_position_ticket(
    destination_ticket: str,
    destination_rows: list[dict[str, Any]],
    gui: Mt5GuiController,
) -> tuple[int, int] | None:
    tickets = [str(row.get("ticket", "")) for row in destination_rows]
    if destination_ticket not in tickets:
        return None

    index = tickets.index(destination_ticket)
    coordinates = gui.config.order_form_coordinates
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


def _mark_destination_canceled(mapping_file: Path, destination_ticket: str) -> None:
    mapping = load_mapping(mapping_file)
    for row in mapping.values():
        if str(row.get("destination_ticket", "")) != destination_ticket:
            continue
        updated = dict(row)
        updated["status"] = "canceled"
        upsert_mapping(mapping_file, updated)
        return


def _mark_destination_closed(mapping_file: Path, destination_ticket: str) -> None:
    mapping = load_mapping(mapping_file)
    for row in mapping.values():
        if str(row.get("destination_ticket", "")) != destination_ticket:
            continue
        updated = dict(row)
        updated["status"] = "closed"
        upsert_mapping(mapping_file, updated)
        return


def _source_position_ticket_for_signature(
    source_positions_file: Path,
    destination: dict[str, Any],
    mapping_file: Path,
) -> str | None:
    target_signature = _position_signature(destination)
    mapped_sources = {
        str(row.get("source_ticket", ""))
        for row in load_mapping(mapping_file).values()
        if row.get("status") == "placed"
    }
    for source in read_csv_rows(source_positions_file):
        source_ticket = str(source.get("ticket", ""))
        if source_ticket in mapped_sources:
            continue
        if _position_signature(source) == target_signature:
            return source_ticket
    return None


def _destination_position_is_still_extra(
    source_positions_file: Path,
    destination_positions_file: Path,
    destination: dict[str, Any],
) -> bool:
    signature = _position_signature(destination)
    source_count = sum(1 for row in read_csv_rows(source_positions_file) if _position_signature(row) == signature)
    destination_count = sum(
        1 for row in read_csv_rows(destination_positions_file) if _position_signature(row) == signature
    )
    return destination_count > source_count


def _order_signature(row: dict[str, Any]) -> tuple[str, ...]:
    volume = row.get("volume_current", row.get("volume_initial", ""))
    return (
        _normalize_signature_text(row.get("symbol")),
        _normalize_signature_text(row.get("type")),
        _normalize_signature_number(volume),
        _normalize_signature_number(row.get("price_open")),
        _normalize_signature_number(row.get("sl")),
        _normalize_signature_number(row.get("tp")),
    )


def _normalize_signature_text(value: Any) -> str:
    return "" if value in {None, ""} else str(value).strip().upper()


def _normalize_signature_number(value: Any) -> str:
    if value in {None, ""}:
        return ""
    try:
        return f"{float(value):.5f}"
    except (TypeError, ValueError):
        return str(value).strip()


def _normalize_number(value: Any) -> float | str:
    if value in {None, ""}:
        return ""
    try:
        return round(float(value), 5)
    except (TypeError, ValueError):
        return str(value)
