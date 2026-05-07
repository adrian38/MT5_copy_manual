import csv
import tempfile
import unittest
from pathlib import Path

from src.mt5_copy.reconciler import find_order_discrepancies, find_position_discrepancies, reconcile_sl_tp


class ReconcilerTest(unittest.TestCase):
    def test_detects_order_sl_tp_mismatch(self):
        source_orders = {"1": {"ticket": "1", "sl": "10.0", "tp": "20.0"}}
        destination_orders = {"2": {"ticket": "2", "sl": "10.0", "tp": "21.0"}}
        mapping = {"1": {"destination_ticket": "2", "status": "placed", "type": "BUY_STOP"}}

        issues = find_order_discrepancies(source_orders, destination_orders, mapping)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, "order_sl_tp_mismatch")
        self.assertEqual(issues[0].field_diffs, {"tp": {"source": "20.0", "destination": "21.0"}})

    def test_detects_position_sl_tp_mismatch(self):
        source_positions = {"10": {"ticket": "10", "sl": "154.500", "tp": "158.500"}}
        destination_positions = {"20": {"ticket": "20", "sl": "155.500", "tp": "157.500"}}
        mapping = {"10": {"destination_ticket": "20", "status": "placed", "type": "BUY"}}

        issues = find_position_discrepancies(source_positions, destination_positions, mapping)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, "position_sl_tp_mismatch")
        self.assertEqual(
            issues[0].field_diffs,
            {
                "sl": {"source": "154.500", "destination": "155.500"},
                "tp": {"source": "158.500", "destination": "157.500"},
            },
        )

    def test_order_and_position_mappings_do_not_cross_reconcile(self):
        mapping = {
            "1": {"destination_ticket": "2", "status": "placed", "type": "BUY_STOP"},
            "10": {"destination_ticket": "20", "status": "placed", "type": "BUY"},
        }

        order_issues = find_order_discrepancies(
            {"1": {"ticket": "1", "sl": "10.0", "tp": "20.0"}},
            {"2": {"ticket": "2", "sl": "10.0", "tp": "21.0"}},
            mapping,
        )
        position_issues = find_position_discrepancies(
            {"10": {"ticket": "10", "sl": "154.500", "tp": "158.500"}},
            {"20": {"ticket": "20", "sl": "155.500", "tp": "157.500"}},
            mapping,
        )

        self.assertEqual([issue.source_ticket for issue in order_issues], ["1"])
        self.assertEqual([issue.source_ticket for issue in position_issues], ["10"])

    def test_reconcile_sl_tp_positions_scope_does_not_modify_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_positions = tmp_path / "source_positions.csv"
            source_orders = tmp_path / "source_orders.csv"
            destination_positions = tmp_path / "destination_positions.csv"
            destination_orders = tmp_path / "destination_orders.csv"
            mapping_file = tmp_path / "mapping.csv"

            _write_rows(source_positions, ["ticket", "sl", "tp"], [{"ticket": "10", "sl": "1", "tp": "2"}])
            _write_rows(source_orders, ["ticket", "sl", "tp"], [{"ticket": "1", "sl": "10", "tp": "20"}])
            _write_rows(
                destination_positions,
                ["ticket", "sl", "tp"],
                [
                    {"ticket": "19", "sl": "1", "tp": "2"},
                    {"ticket": "20", "sl": "1", "tp": "0"},
                ],
            )
            _write_rows(destination_orders, ["ticket", "sl", "tp"], [{"ticket": "2", "sl": "10", "tp": "0"}])
            _write_rows(
                mapping_file,
                ["source_ticket", "destination_ticket", "symbol", "type", "source_volume", "destination_volume", "status"],
                [
                    {"source_ticket": "10", "destination_ticket": "20", "symbol": "XAUUSD", "type": "BUY", "source_volume": "0.01", "destination_volume": "0.01", "status": "placed"},
                    {"source_ticket": "1", "destination_ticket": "2", "symbol": "XAUUSD", "type": "BUY_STOP", "source_volume": "0.01", "destination_volume": "0.01", "status": "placed"},
                ],
            )

            gui = FakeGui()
            remaining = reconcile_sl_tp(
                source_positions,
                source_orders,
                destination_positions,
                destination_orders,
                mapping_file,
                gui,
                FakeLogger(),
                max_retries=1,
                verify_delay_seconds=0,
                issue_scope="positions",
            )

            self.assertEqual(gui.modified_positions, [("20", 1, 2, (253, 741))])
            self.assertEqual(gui.modified_orders, [])
            self.assertEqual([issue.issue_type for issue in remaining], ["position_sl_tp_mismatch"])


if __name__ == "__main__":
    unittest.main()


class FakeGui:
    def __init__(self):
        self.modified_positions = []
        self.modified_orders = []
        self.config = type(
            "Config",
            (),
            {
                "order_form_coordinates": {
                    "position_row_anchor": (253, 721),
                    "position_row_step_y": (0, 20),
                    "position_row_max_y": (0, 941),
                }
            },
        )()

    def modify_position_sl_tp(self, destination_ticket, sl, tp, row_center=None):
        self.modified_positions.append((destination_ticket, sl, tp, row_center))
        return Path("position.png")

    def modify_pending_order_sl_tp(self, destination_ticket, sl, tp):
        self.modified_orders.append((destination_ticket, sl, tp))
        return Path("order.png")


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _write_rows(path, fieldnames, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
