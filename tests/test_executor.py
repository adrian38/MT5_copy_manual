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
            {"order_form_coordinates": {"order_row_anchor": (253, 741), "order_row_step_y": (0, 20)}},
        )()
        self.deleted_tickets = []
        self.row_centers = []

    def delete_pending_order(self, destination_ticket: str, row_center=None):
        self.deleted_tickets.append(destination_ticket)
        self.row_centers.append(row_center)
        return Path("screenshot.png")


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
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


if __name__ == "__main__":
    unittest.main()
