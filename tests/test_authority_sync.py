import csv
import tempfile
import unittest
from pathlib import Path

from src.mt5_copy.reconciler import reconcile_orders_to_source_authority


FIELDNAMES = ["ticket", "symbol", "type", "volume_current", "price_open", "sl", "tp"]


class FakeConfig:
    submit_orders = True
    order_form_coordinates = {"order_row_anchor": (253, 741), "order_row_step_y": (0, 20)}


class FakeGui:
    def __init__(self, destination_file: Path):
        self.config = FakeConfig()
        self.destination_file = destination_file
        self.deleted = []
        self.created = []
        self.next_ticket = 900

    def prepare_pending_order(self, order):
        self.next_ticket += 1
        self.created.append(str(order["ticket"]))
        rows = _read_rows(self.destination_file)
        created = dict(order)
        created["ticket"] = str(self.next_ticket)
        rows.append(created)
        _write_rows(self.destination_file, rows)
        return {}

    def delete_pending_order(self, destination_ticket, row_center=None):
        self.deleted.append(destination_ticket)
        rows = [row for row in _read_rows(self.destination_file) if row["ticket"] != destination_ticket]
        _write_rows(self.destination_file, rows)
        return Path("deleted.png")


class RaceSourceAppearsGui(FakeGui):
    def __init__(self, source_file: Path, destination_file: Path, appearing_source: dict):
        super().__init__(destination_file)
        self.source_file = source_file
        self.appearing_source = appearing_source

    def prepare_pending_order(self, order):
        raise AssertionError("No creation expected in this race test")


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class AuthoritySyncTest(unittest.TestCase):
    def test_sync_creates_missing_and_deletes_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_file = Path(tmp) / "source.csv"
            destination_file = Path(tmp) / "destination.csv"
            mapping_file = Path(tmp) / "mapping.csv"

            _write_rows(
                source_file,
                [
                    _row("1", "BUY_STOP", "0.01", "100", "90", "120"),
                    _row("2", "SELL_STOP", "0.02", "80", "90", "70"),
                ],
            )
            _write_rows(destination_file, [_row("10", "BUY_STOP", "0.01", "100", "90", "120"), _row("11", "SELL_STOP", "0.03", "60", "70", "50")])
            _write_mapping(mapping_file)

            gui = FakeGui(destination_file)
            report = reconcile_orders_to_source_authority(
                source_file,
                destination_file,
                mapping_file,
                gui,
                FakeLogger(),
                verify_delay_seconds=0,
            )

            self.assertEqual(report.created, 1)
            self.assertEqual(report.deleted, 1)
            self.assertEqual(gui.created, ["2"])
            self.assertEqual(gui.deleted, ["11"])
            signatures = {_signature(row) for row in _read_rows(destination_file)}
            self.assertEqual(
                signatures,
                {
                    ("BUY_STOP", "0.01", "100", "90", "120"),
                    ("SELL_STOP", "0.02", "80", "90", "70"),
                },
            )

    def test_delete_skips_if_source_appears_before_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_file = Path(tmp) / "source.csv"
            destination_file = Path(tmp) / "destination.csv"
            mapping_file = Path(tmp) / "mapping.csv"

            appearing = _row("1", "SELL_STOP", "0.03", "80", "90", "70")
            _write_rows(source_file, [])
            _write_rows(destination_file, [_row("10", "SELL_STOP", "0.03", "80", "90", "70")])
            _write_mapping(mapping_file)

            gui = RaceSourceAppearsGui(source_file, destination_file, appearing)
            def make_source_appear(_destination_ticket):
                _write_rows(source_file, [appearing])

            report = reconcile_orders_to_source_authority(
                source_file,
                destination_file,
                mapping_file,
                gui,
                FakeLogger(),
                verify_delay_seconds=0,
                before_delete_check=make_source_appear,
            )

            self.assertEqual(report.deleted, 0)
            self.assertEqual(gui.deleted, [])
            self.assertIn("delete:10:now_matches_source", report.skipped)


def _row(ticket, trade_type, volume, price, sl, tp):
    return {
        "ticket": ticket,
        "symbol": "XAUUSD",
        "type": trade_type,
        "volume_current": volume,
        "price_open": price,
        "sl": sl,
        "tp": tp,
    }


def _signature(row):
    return (row["type"], row["volume_current"], row["price_open"], row["sl"], row["tp"])


def _read_rows(path):
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_rows(path, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _write_mapping(path):
    with path.open("w", encoding="utf-8", newline="") as fh:
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


if __name__ == "__main__":
    unittest.main()
