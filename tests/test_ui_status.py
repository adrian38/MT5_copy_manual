import csv
import tempfile
import unittest
from pathlib import Path

from src.mt5_copy.ui import read_terminal_status


class UiStatusTest(unittest.TestCase):
    def test_heartbeat_running_file_is_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            heartbeat = Path(tmp) / "heartbeat.csv"
            with heartbeat.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "status",
                        "server_time",
                        "account_login",
                        "account_server",
                        "positions_total",
                        "orders_total",
                    ],
                    delimiter="\t",
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "status": "RUNNING",
                        "server_time": "2026.05.06 20:00:00",
                        "account_login": "123",
                        "account_server": "Demo",
                        "positions_total": "1",
                        "orders_total": "2",
                    }
                )

            status = read_terminal_status("Master", heartbeat, stale_after_seconds=60)

            self.assertTrue(status.active)
            self.assertEqual(status.account_login, "123")
            self.assertEqual(status.positions_total, "1")
            self.assertEqual(status.orders_total, "2")

    def test_missing_heartbeat_is_inactive(self):
        status = read_terminal_status("Destino", Path("does-not-exist.csv"), stale_after_seconds=60)

        self.assertFalse(status.active)
        self.assertEqual(status.status, "NO_HEARTBEAT")


if __name__ == "__main__":
    unittest.main()
