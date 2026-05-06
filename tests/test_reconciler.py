import unittest

from src.mt5_copy.reconciler import find_order_discrepancies, find_position_discrepancies


class ReconcilerTest(unittest.TestCase):
    def test_detects_order_sl_tp_mismatch(self):
        source_orders = {"1": {"ticket": "1", "sl": "10.0", "tp": "20.0"}}
        destination_orders = {"2": {"ticket": "2", "sl": "10.0", "tp": "21.0"}}
        mapping = {"1": {"destination_ticket": "2", "status": "placed"}}

        issues = find_order_discrepancies(source_orders, destination_orders, mapping)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, "order_sl_tp_mismatch")
        self.assertEqual(issues[0].field_diffs, {"tp": {"source": "20.0", "destination": "21.0"}})

    def test_detects_position_sl_tp_mismatch(self):
        source_positions = {"10": {"ticket": "10", "sl": "154.500", "tp": "158.500"}}
        destination_positions = {"20": {"ticket": "20", "sl": "155.500", "tp": "157.500"}}
        mapping = {"10": {"destination_ticket": "20", "status": "placed"}}

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


if __name__ == "__main__":
    unittest.main()
