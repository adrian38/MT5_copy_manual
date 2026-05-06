import unittest

from src.mt5_copy.detector import detect_changes
from src.mt5_copy.models import ChangeType


class DetectorTest(unittest.TestCase):
    def test_detects_order_created_updated_and_deleted(self):
        previous_orders = {
            "1": {"ticket": 1, "symbol": "XAUUSD", "type": "BUY_STOP", "sl": 10.0, "tp": 20.0},
            "2": {"ticket": 2, "symbol": "XAUUSD", "type": "SELL_STOP", "sl": 30.0, "tp": 40.0},
        }
        current_orders = {
            "1": {"ticket": 1, "symbol": "XAUUSD", "type": "BUY_STOP", "sl": 11.0, "tp": 20.0},
            "3": {"ticket": 3, "symbol": "EURUSD", "type": "BUY_LIMIT", "sl": 1.0, "tp": 2.0},
        }

        events = detect_changes({}, {}, previous_orders, current_orders)

        self.assertEqual(
            [event.change_type for event in events],
            [
                ChangeType.ORDER_UPDATED,
                ChangeType.ORDER_CREATED,
                ChangeType.ORDER_DELETED,
            ],
        )
        self.assertEqual(events[0].changed_fields, {"sl": {"from": 10.0, "to": 11.0}})

    def test_detects_position_open_update_close(self):
        previous_positions = {
            "10": {"ticket": 10, "symbol": "EURUSD", "type": "BUY", "sl": 1.1, "tp": 1.2},
            "11": {"ticket": 11, "symbol": "EURUSD", "type": "SELL", "sl": 1.3, "tp": 1.0},
        }
        current_positions = {
            "10": {"ticket": 10, "symbol": "EURUSD", "type": "BUY", "sl": 1.1, "tp": 1.25},
            "12": {"ticket": 12, "symbol": "GBPUSD", "type": "BUY", "sl": 1.0, "tp": 1.1},
        }

        events = detect_changes(previous_positions, current_positions, {}, {})

        self.assertEqual(
            [event.change_type for event in events],
            [
                ChangeType.POSITION_UPDATED,
                ChangeType.POSITION_OPENED,
                ChangeType.POSITION_CLOSED,
            ],
        )
        self.assertEqual(events[0].changed_fields, {"tp": {"from": 1.2, "to": 1.25}})


if __name__ == "__main__":
    unittest.main()
