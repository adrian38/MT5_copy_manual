import unittest

from src.mt5_copy.app import _authority_sync_flags, _sl_tp_reconcile_scope
from src.mt5_copy.models import ChangeEvent, ChangeType


class AppTest(unittest.TestCase):
    def test_position_update_only_runs_position_authority_sync(self):
        events = [
            ChangeEvent(
                change_type=ChangeType.POSITION_UPDATED,
                source_ticket="1",
                symbol="XAUUSD",
                trade_type="BUY",
                previous={"tp": "10"},
                current={"tp": "11"},
                changed_fields={"tp": {"from": "10", "to": "11"}},
            )
        ]

        self.assertEqual(_authority_sync_flags(events, "auto"), (False, False))
        self.assertEqual(_sl_tp_reconcile_scope(events), "positions")

    def test_no_events_runs_full_authority_sync(self):
        self.assertEqual(_authority_sync_flags([], "auto"), (True, True))

    def test_position_update_without_sl_tp_does_not_reconcile_sl_tp(self):
        events = [
            ChangeEvent(
                change_type=ChangeType.POSITION_UPDATED,
                source_ticket="1",
                symbol="XAUUSD",
                trade_type="BUY",
                previous={"profit": "1"},
                current={"profit": "2"},
                changed_fields={"profit": {"from": "1", "to": "2"}},
            )
        ]

        self.assertIsNone(_sl_tp_reconcile_scope(events))


if __name__ == "__main__":
    unittest.main()
