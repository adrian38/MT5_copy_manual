import csv
import tempfile
import unittest
from pathlib import Path

from src.mt5_copy.reconciler import reconcile_orders_to_source_authority, reconcile_positions_to_source_authority


FIELDNAMES = ["ticket", "symbol", "type", "volume_current", "price_open", "sl", "tp"]


class FakeConfig:
    submit_orders = True
    order_form_coordinates = {
        "order_row_anchor": (253, 741),
        "order_row_step_y": (0, 20),
        "position_row_anchor": (253, 721),
        "position_row_step_y": (0, 20),
        "position_row_max_y": (0, 941),
    }


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


class FakePositionGui:
    def __init__(self, destination_file: Path):
        self.config = FakeConfig()
        self.destination_file = destination_file
        self.closed = []
        self.row_centers = []
        self.created = []
        self.next_ticket = 990

    def prepare_market_position(self, position):
        self.next_ticket += 1
        self.created.append(str(position["ticket"]))
        rows = _read_position_rows(self.destination_file)
        created = dict(position)
        created["ticket"] = str(self.next_ticket)
        rows.append(created)
        _write_position_rows(self.destination_file, rows)
        return {}

    def close_position(self, destination_ticket, row_center=None, trade_type=None):
        self.closed.append(destination_ticket)
        self.row_centers.append(row_center)
        rows = [row for row in _read_position_rows(self.destination_file) if row["ticket"] != destination_ticket]
        _write_position_rows(self.destination_file, rows)
        return Path("position_closed.png")


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

    def test_deletes_duplicate_even_when_signature_exists_in_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_file = Path(tmp) / "source.csv"
            destination_file = Path(tmp) / "destination.csv"
            mapping_file = Path(tmp) / "mapping.csv"

            _write_rows(source_file, [_row("1", "BUY_STOP", "0.01", "100", "90", "120")])
            _write_rows(
                destination_file,
                [
                    _row("10", "BUY_STOP", "0.01", "100", "90", "120"),
                    _row("11", "BUY_STOP", "0.01", "100", "90", "120"),
                ],
            )
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

            self.assertEqual(report.deleted, 1)
            self.assertEqual(gui.deleted, ["11"])
            self.assertEqual(len(_read_rows(destination_file)), 1)

    def test_position_sync_closes_extra_destination_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_file = Path(tmp) / "source_positions.csv"
            destination_file = Path(tmp) / "destination_positions.csv"
            mapping_file = Path(tmp) / "mapping.csv"

            _write_position_rows(source_file, [_position_row("1", "BUY", "0.01")])
            _write_position_rows(
                destination_file,
                [
                    _position_row("10", "SELL", "0.01"),
                    _position_row("11", "BUY", "0.01"),
                ],
            )
            _write_mapping(mapping_file)

            gui = FakePositionGui(destination_file)
            report = reconcile_positions_to_source_authority(
                source_file,
                destination_file,
                mapping_file,
                gui,
                FakeLogger(),
                verify_delay_seconds=0,
            )

            self.assertEqual(report.deleted, 1)
            self.assertEqual(gui.closed, ["10"])
            self.assertEqual(gui.row_centers, [(253, 721)])
            self.assertEqual([row["ticket"] for row in _read_position_rows(destination_file)], ["11"])

    def test_position_sync_does_not_reopen_missing_mapped_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_file = Path(tmp) / "source_positions.csv"
            destination_file = Path(tmp) / "destination_positions.csv"
            mapping_file = Path(tmp) / "mapping.csv"

            _write_position_rows(source_file, [_position_row("1", "BUY", "0.01", sl="4600")])
            _write_position_rows(destination_file, [])
            _write_mapping(
                mapping_file,
                [
                    {
                        "source_ticket": "1",
                        "destination_ticket": "10",
                        "symbol": "XAUUSD",
                        "type": "BUY",
                        "source_volume": "0.01",
                        "destination_volume": "0.01",
                        "status": "placed",
                    }
                ],
            )

            gui = FakePositionGui(destination_file)
            report = reconcile_positions_to_source_authority(
                source_file,
                destination_file,
                mapping_file,
                gui,
                FakeLogger(),
                verify_delay_seconds=0,
            )

            self.assertEqual(report.created, 0)
            self.assertEqual(gui.created, [])
            self.assertIn("create_position:1:mapped_destination_missing_manual_review", report.skipped)


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


def _position_row(ticket, trade_type, volume, sl="0", tp="0"):
    return {
        "ticket": ticket,
        "symbol": "XAUUSD",
        "type": trade_type,
        "volume": volume,
        "price_open": "4700",
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


def _read_position_rows(path):
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_position_rows(path, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["ticket", "symbol", "type", "volume", "price_open", "sl", "tp"])
        writer.writeheader()
        writer.writerows(rows)


def _write_mapping(path, rows=None):
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
        if rows:
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
