import csv
import tempfile
import unittest
from pathlib import Path

from src.mt5_copy.executor import PyAutoGuiExecutor
from src.mt5_copy.models import ChangeEvent, ChangeType


class FakeGui:
    def __init__(self):
        self.config = type(
            "Config",
            (),
            {
                "order_form_coordinates": {"order_row_anchor": (253, 741), "order_row_step_y": (0, 20)},
                "order_window_delay_seconds": 0,
            },
        )()
        self.deleted_tickets = []
        self.row_centers = []
        self.closed_positions = []
        self.position_row_centers = []
        self.prepared_pending_orders = []

    def delete_pending_order(self, destination_ticket: str, row_center=None):
        self.deleted_tickets.append(destination_ticket)
        self.row_centers.append(row_center)
        return Path("screenshot.png")

    def prepare_pending_order(self, order):
        self.prepared_pending_orders.append(order)
        return {
            "source_ticket": order.get("ticket", ""),
            "symbol": order.get("symbol", ""),
            "type": order.get("type", ""),
            "volume": order.get("volume_current", order.get("volume_initial", "")),
            "price_open": order.get("price_open", ""),
            "sl": order.get("sl", ""),
            "tp": order.get("tp", ""),
            "screenshot_order_window": "pending.png",
        }

    def close_position(self, destination_ticket: str, row_center=None, trade_type=None):
        self.closed_positions.append(destination_ticket)
        self.position_row_centers.append(row_center)
        return Path("position_closed.png")


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class ExecutorTest(unittest.TestCase):
    def test_order_deleted_executes_destination_delete_and_updates_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            mapping_file = Path(tmp) / "mapping.csv"
            with mapping_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "source_ticket",
                        "destination_ticket",
                        "symbol",
                        "type",
                        "source_volume",
                        "destination_volume",
                        "status",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "source_ticket": "1",
                        "destination_ticket": "2",
                        "symbol": "XAUUSD",
                        "type": "BUY_STOP",
                        "source_volume": "0.01",
                        "destination_volume": "0.01",
                        "status": "placed",
                    }
                )

            gui = FakeGui()
            destination_orders_file = Path(tmp) / "destination_orders.csv"
            with destination_orders_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=["ticket", "symbol", "type", "volume_current", "price_open", "sl", "tp"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "ticket": "2",
                        "symbol": "XAUUSD",
                        "type": "BUY_STOP",
                        "volume_current": "0.01",
                        "price_open": "100.0",
                        "sl": "90.0",
                        "tp": "120.0",
                    }
                )

            executor = PyAutoGuiExecutor(
                gui,
                FakeLogger(),
                mapping_file=mapping_file,
                destination_orders_file=destination_orders_file,
            )
            executor.handle(
                ChangeEvent(
                    change_type=ChangeType.ORDER_DELETED,
                    source_ticket="1",
                    symbol="XAUUSD",
                    trade_type="BUY_STOP",
                    previous={
                        "symbol": "XAUUSD",
                        "type": "BUY_STOP",
                        "volume_current": "0.01",
                        "price_open": "100.0",
                        "sl": "90.0",
                        "tp": "120.0",
                    },
                    current=None,
                    changed_fields={},
                )
            )

            self.assertEqual(gui.deleted_tickets, ["2"])
            self.assertEqual(gui.row_centers, [(253, 741)])
            with mapping_file.open("r", encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows[0]["status"], "canceled")

    def test_order_created_maps_verified_pending_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            mapping_file = Path(tmp) / "mapping.csv"
            destination_orders_file = Path(tmp) / "destination_orders.csv"
            with destination_orders_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=["ticket", "symbol", "type", "volume_current", "price_open", "sl", "tp"],
                )
                writer.writeheader()

            class PendingGui(FakeGui):
                def prepare_pending_order(self, order):
                    with destination_orders_file.open("a", encoding="utf-8", newline="") as fh:
                        writer = csv.DictWriter(
                            fh,
                            fieldnames=["ticket", "symbol", "type", "volume_current", "price_open", "sl", "tp"],
                        )
                        writer.writerow(
                            {
                                "ticket": "77",
                                "symbol": order["symbol"],
                                "type": order["type"],
                                "volume_current": order["volume_current"],
                                "price_open": order["price_open"],
                                "sl": order["sl"],
                                "tp": order["tp"],
                            }
                        )
                    return super().prepare_pending_order(order)

            executor = PyAutoGuiExecutor(
                PendingGui(),
                FakeLogger(),
                mapping_file=mapping_file,
                destination_orders_file=destination_orders_file,
            )
            executor.handle(
                ChangeEvent(
                    change_type=ChangeType.ORDER_CREATED,
                    source_ticket="1",
                    symbol="XAUUSD",
                    trade_type="BUY_STOP",
                    previous=None,
                    current={
                        "ticket": "1",
                        "symbol": "XAUUSD",
                        "type": "BUY_STOP",
                        "volume_current": "0.01",
                        "price_open": "100.0",
                        "sl": "90.0",
                        "tp": "120.0",
                    },
                    changed_fields={},
                )
            )

            with mapping_file.open("r", encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows[0]["source_ticket"], "1")
            self.assertEqual(rows[0]["destination_ticket"], "77")
            self.assertEqual(rows[0]["type"], "BUY_STOP")
            self.assertEqual(rows[0]["status"], "placed")

    def test_order_deleted_skips_when_destination_does_not_match_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            mapping_file = Path(tmp) / "mapping.csv"
            with mapping_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "source_ticket",
                        "destination_ticket",
                        "symbol",
                        "type",
                        "source_volume",
                        "destination_volume",
                        "status",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "source_ticket": "1",
                        "destination_ticket": "2",
                        "symbol": "XAUUSD",
                        "type": "BUY_STOP",
                        "source_volume": "0.01",
                        "destination_volume": "0.01",
                        "status": "placed",
                    }
                )

            destination_orders_file = Path(tmp) / "destination_orders.csv"
            with destination_orders_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=["ticket", "symbol", "type", "volume_current", "price_open", "sl", "tp"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "ticket": "2",
                        "symbol": "XAUUSD",
                        "type": "SELL_STOP",
                        "volume_current": "0.01",
                        "price_open": "100.0",
                        "sl": "90.0",
                        "tp": "120.0",
                    }
                )

            gui = FakeGui()
            executor = PyAutoGuiExecutor(
                gui,
                FakeLogger(),
                mapping_file=mapping_file,
                destination_orders_file=destination_orders_file,
            )
            executor.handle(
                ChangeEvent(
                    change_type=ChangeType.ORDER_DELETED,
                    source_ticket="1",
                    symbol="XAUUSD",
                    trade_type="BUY_STOP",
                    previous={
                        "symbol": "XAUUSD",
                        "type": "BUY_STOP",
                        "volume_current": "0.01",
                        "price_open": "100.0",
                        "sl": "90.0",
                        "tp": "120.0",
                    },
                    current=None,
                    changed_fields={},
                )
            )

            self.assertEqual(gui.deleted_tickets, [])

    def test_order_deleted_uses_csv_order_top_to_bottom_for_row_center(self):
        with tempfile.TemporaryDirectory() as tmp:
            mapping_file = Path(tmp) / "mapping.csv"
            with mapping_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "source_ticket",
                        "destination_ticket",
                        "symbol",
                        "type",
                        "source_volume",
                        "destination_volume",
                        "status",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "source_ticket": "1",
                        "destination_ticket": "3",
                        "symbol": "XAUUSD",
                        "type": "BUY_STOP",
                        "source_volume": "0.01",
                        "destination_volume": "0.01",
                        "status": "placed",
                    }
                )

            destination_orders_file = Path(tmp) / "destination_orders.csv"
            with destination_orders_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=["ticket", "symbol", "type", "volume_current", "price_open", "sl", "tp"],
                )
                writer.writeheader()
                writer.writerow({"ticket": "2", "symbol": "XAUUSD", "type": "BUY_STOP", "volume_current": "0.01", "price_open": "99", "sl": "90", "tp": "120"})
                writer.writerow({"ticket": "3", "symbol": "XAUUSD", "type": "BUY_STOP", "volume_current": "0.01", "price_open": "100", "sl": "90", "tp": "120"})

            gui = FakeGui()
            executor = PyAutoGuiExecutor(
                gui,
                FakeLogger(),
                mapping_file=mapping_file,
                destination_orders_file=destination_orders_file,
            )
            executor.handle(
                ChangeEvent(
                    change_type=ChangeType.ORDER_DELETED,
                    source_ticket="1",
                    symbol="XAUUSD",
                    trade_type="BUY_STOP",
                    previous={
                        "symbol": "XAUUSD",
                        "type": "BUY_STOP",
                        "volume_current": "0.01",
                        "price_open": "100",
                        "sl": "90",
                        "tp": "120",
                    },
                    current=None,
                    changed_fields={},
                )
            )

            self.assertEqual(gui.row_centers, [(253, 761)])

    def test_position_opened_maps_verified_market_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            mapping_file = Path(tmp) / "mapping.csv"
            destination_positions_file = Path(tmp) / "destination_positions.csv"
            with destination_positions_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=["ticket", "symbol", "type", "volume", "price_open", "sl", "tp"],
                )
                writer.writeheader()

            class MarketGui(FakeGui):
                def prepare_market_position(self, position):
                    with destination_positions_file.open("a", encoding="utf-8", newline="") as fh:
                        writer = csv.DictWriter(
                            fh,
                            fieldnames=["ticket", "symbol", "type", "volume", "price_open", "sl", "tp"],
                        )
                        writer.writerow(
                            {
                                "ticket": "99",
                                "symbol": position["symbol"],
                                "type": position["type"],
                                "volume": position["volume"],
                                "price_open": "4745.12",
                                "sl": position["sl"],
                                "tp": position["tp"],
                            }
                        )
                    return {
                        "source_ticket": position["ticket"],
                        "symbol": position["symbol"],
                        "type": position["type"],
                        "volume": position["volume"],
                        "sl": position["sl"],
                        "tp": position["tp"],
                        "screenshot_order_window": "screenshot.png",
                    }

            executor = PyAutoGuiExecutor(
                MarketGui(),
                FakeLogger(),
                mapping_file=mapping_file,
                destination_positions_file=destination_positions_file,
            )
            executor.handle(
                ChangeEvent(
                    change_type=ChangeType.POSITION_OPENED,
                    source_ticket="42",
                    symbol="XAUUSD",
                    trade_type="BUY",
                    previous=None,
                    current={
                        "ticket": "42",
                        "symbol": "XAUUSD",
                        "type": "BUY",
                        "volume": "0.01",
                        "price_open": "4686.09",
                        "sl": "0.00",
                        "tp": "0.00",
                    },
                    changed_fields={},
                )
            )

            with mapping_file.open("r", encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows[0]["source_ticket"], "42")
            self.assertEqual(rows[0]["destination_ticket"], "99")
            self.assertEqual(rows[0]["type"], "BUY")

    def test_position_opened_adopts_triggered_pending_without_opening_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            mapping_file = Path(tmp) / "mapping.csv"
            with mapping_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "source_ticket",
                        "destination_ticket",
                        "symbol",
                        "type",
                        "source_volume",
                        "destination_volume",
                        "status",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "source_ticket": "10",
                        "destination_ticket": "20",
                        "symbol": "XAUUSD",
                        "type": "BUY_STOP",
                        "source_volume": "0.01",
                        "destination_volume": "0.01",
                        "status": "placed",
                    }
                )

            destination_orders_file = Path(tmp) / "destination_orders.csv"
            with destination_orders_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["ticket", "symbol", "type", "volume_current"])
                writer.writeheader()

            destination_positions_file = Path(tmp) / "destination_positions.csv"
            with destination_positions_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["ticket", "symbol", "type", "volume", "price_open", "sl", "tp"])
                writer.writeheader()
                writer.writerow(
                    {
                        "ticket": "30",
                        "symbol": "XAUUSD",
                        "type": "BUY",
                        "volume": "0.01",
                        "price_open": "4700",
                        "sl": "0",
                        "tp": "0",
                    }
                )

            class NoDuplicateGui(FakeGui):
                def __init__(self):
                    super().__init__()
                    self.market_prepares = 0

                def prepare_market_position(self, position):
                    self.market_prepares += 1
                    raise AssertionError("should not open a duplicate market position")

            gui = NoDuplicateGui()
            executor = PyAutoGuiExecutor(
                gui,
                FakeLogger(),
                mapping_file=mapping_file,
                destination_orders_file=destination_orders_file,
                destination_positions_file=destination_positions_file,
            )
            executor.handle(
                ChangeEvent(
                    change_type=ChangeType.POSITION_OPENED,
                    source_ticket="11",
                    symbol="XAUUSD",
                    trade_type="BUY",
                    previous=None,
                    current={
                        "ticket": "11",
                        "symbol": "XAUUSD",
                        "type": "BUY",
                        "volume": "0.01",
                        "price_open": "4700",
                        "sl": "0",
                        "tp": "0",
                    },
                    changed_fields={},
                )
            )

            self.assertEqual(gui.market_prepares, 0)
            with mapping_file.open("r", encoding="utf-8", newline="") as fh:
                rows = {row["source_ticket"]: row for row in csv.DictReader(fh)}
            self.assertEqual(rows["10"]["status"], "triggered")
            self.assertEqual(rows["11"]["destination_ticket"], "30")
            self.assertEqual(rows["11"]["type"], "BUY")
            self.assertEqual(rows["11"]["status"], "placed")

    def test_position_closed_closes_destination_and_updates_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            mapping_file = Path(tmp) / "mapping.csv"
            with mapping_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "source_ticket",
                        "destination_ticket",
                        "symbol",
                        "type",
                        "source_volume",
                        "destination_volume",
                        "status",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "source_ticket": "42",
                        "destination_ticket": "99",
                        "symbol": "XAUUSD",
                        "type": "BUY",
                        "source_volume": "0.01",
                        "destination_volume": "0.01",
                        "status": "placed",
                    }
                )

            destination_positions_file = Path(tmp) / "destination_positions.csv"
            with destination_positions_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["ticket", "symbol", "type", "volume"])
                writer.writeheader()
                writer.writerow({"ticket": "99", "symbol": "XAUUSD", "type": "BUY", "volume": "0.01"})

            class ClosingGui(FakeGui):
                def close_position(self, destination_ticket: str, row_center=None, trade_type=None):
                    self.closed_positions.append(destination_ticket)
                    self.position_row_centers.append(row_center)
                    with destination_positions_file.open("w", encoding="utf-8", newline="") as out_fh:
                        writer = csv.DictWriter(out_fh, fieldnames=["ticket", "symbol", "type", "volume"])
                        writer.writeheader()
                    return Path("position_closed.png")

            gui = ClosingGui()
            executor = PyAutoGuiExecutor(
                gui,
                FakeLogger(),
                mapping_file=mapping_file,
                destination_positions_file=destination_positions_file,
            )
            executor.handle(
                ChangeEvent(
                    change_type=ChangeType.POSITION_CLOSED,
                    source_ticket="42",
                    symbol="XAUUSD",
                    trade_type="BUY",
                    previous={"ticket": "42", "symbol": "XAUUSD", "type": "BUY", "volume": "0.01"},
                    current=None,
                    changed_fields={},
                )
            )

            self.assertEqual(gui.closed_positions, ["99"])
            self.assertEqual(gui.position_row_centers, [(253, 721)])
            with mapping_file.open("r", encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows[0]["status"], "closed")

    def test_position_closed_falls_back_to_context_menu_when_x_close_does_not_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            mapping_file = Path(tmp) / "mapping.csv"
            with mapping_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "source_ticket",
                        "destination_ticket",
                        "symbol",
                        "type",
                        "source_volume",
                        "destination_volume",
                        "status",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "source_ticket": "42",
                        "destination_ticket": "99",
                        "symbol": "XAUUSD",
                        "type": "BUY",
                        "source_volume": "0.01",
                        "destination_volume": "0.01",
                        "status": "placed",
                    }
                )

            destination_positions_file = Path(tmp) / "destination_positions.csv"
            with destination_positions_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["ticket", "symbol", "type", "volume"])
                writer.writeheader()
                writer.writerow({"ticket": "99", "symbol": "XAUUSD", "type": "BUY", "volume": "0.01"})

            class ContextClosingGui(FakeGui):
                def __init__(self):
                    super().__init__()
                    self.context_closed = []

                def close_position(self, destination_ticket: str, row_center=None, trade_type=None):
                    self.closed_positions.append(destination_ticket)
                    self.position_row_centers.append(row_center)
                    return Path("position_x_close_clicked.png")

                def close_position_from_context_menu(self, destination_ticket: str, row_center=None, trade_type=None):
                    self.context_closed.append((destination_ticket, row_center, trade_type))
                    with destination_positions_file.open("w", encoding="utf-8", newline="") as out_fh:
                        writer = csv.DictWriter(out_fh, fieldnames=["ticket", "symbol", "type", "volume"])
                        writer.writeheader()
                    return Path("position_context_closed.png")

            gui = ContextClosingGui()
            executor = PyAutoGuiExecutor(
                gui,
                FakeLogger(),
                mapping_file=mapping_file,
                destination_positions_file=destination_positions_file,
            )
            executor.handle(
                ChangeEvent(
                    change_type=ChangeType.POSITION_CLOSED,
                    source_ticket="42",
                    symbol="XAUUSD",
                    trade_type="BUY",
                    previous={"ticket": "42", "symbol": "XAUUSD", "type": "BUY", "volume": "0.01"},
                    current=None,
                    changed_fields={},
                )
            )

            self.assertEqual(gui.closed_positions, ["99"])
            self.assertEqual(gui.context_closed, [("99", (253, 721), "BUY")])
            with mapping_file.open("r", encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows[0]["status"], "closed")

    def test_position_closed_refuses_when_destination_row_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            mapping_file = Path(tmp) / "mapping.csv"
            with mapping_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "source_ticket",
                        "destination_ticket",
                        "symbol",
                        "type",
                        "source_volume",
                        "destination_volume",
                        "status",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "source_ticket": "42",
                        "destination_ticket": "99",
                        "symbol": "XAUUSD",
                        "type": "BUY",
                        "source_volume": "0.01",
                        "destination_volume": "0.01",
                        "status": "placed",
                    }
                )

            destination_positions_file = Path(tmp) / "destination_positions.csv"
            with destination_positions_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["ticket", "symbol", "type", "volume"])
                writer.writeheader()
                writer.writerow({"ticket": "88", "symbol": "XAUUSD", "type": "SELL", "volume": "0.01"})

            gui = FakeGui()
            executor = PyAutoGuiExecutor(
                gui,
                FakeLogger(),
                mapping_file=mapping_file,
                destination_positions_file=destination_positions_file,
            )
            executor.handle(
                ChangeEvent(
                    change_type=ChangeType.POSITION_CLOSED,
                    source_ticket="42",
                    symbol="XAUUSD",
                    trade_type="BUY",
                    previous={"ticket": "42", "symbol": "XAUUSD", "type": "BUY", "volume": "0.01"},
                    current=None,
                    changed_fields={},
                )
            )

            self.assertEqual(gui.closed_positions, [])
            with mapping_file.open("r", encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows[0]["status"], "closed")

    def test_market_order_created_is_ignored_as_transient_order_row(self):
        gui = FakeGui()
        executor = PyAutoGuiExecutor(gui, FakeLogger())
        executor.handle(
            ChangeEvent(
                change_type=ChangeType.ORDER_CREATED,
                source_ticket="42",
                symbol="XAUUSD",
                trade_type="BUY",
                previous=None,
                current={
                    "ticket": "42",
                    "symbol": "XAUUSD",
                    "type": "BUY",
                    "volume_current": "0.01",
                    "price_open": "4700",
                    "sl": "0",
                    "tp": "0",
                },
                changed_fields={},
            )
        )

        self.assertEqual(gui.prepared_pending_orders, [])


if __name__ == "__main__":
    unittest.main()
