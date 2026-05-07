import csv
import tempfile
import unittest
from pathlib import Path

from src.mt5_copy.ui import apply_automatic_coordinate_calibration, read_terminal_status


class UiStatusTest(unittest.TestCase):
    def test_automatic_calibration_repairs_executor_when_resolution_matches_baseline(self):
        raw = {
            "executor": {
                "new_order_button": [348, 42],
                "order_form_coordinates": {
                    "symbol": [825, 263],
                    "order_scan_rows": [0, 12],
                },
            },
            "coordinate_baseline": {
                "resolution": [1920, 1080],
                "new_order_button": [489, 59],
                "order_form_coordinates": {
                    "symbol": [1160, 370],
                    "order_scan_rows": [0, 12],
                },
            },
        }

        base_w, base_h, scale_x, scale_y = apply_automatic_coordinate_calibration(raw, 1920, 1080)

        self.assertEqual((base_w, base_h), (1920, 1080))
        self.assertEqual((scale_x, scale_y), (1.0, 1.0))
        self.assertEqual(raw["executor"]["new_order_button"], [489, 59])
        self.assertEqual(raw["executor"]["order_form_coordinates"]["symbol"], [1160, 370])
        self.assertEqual(raw["executor"]["order_form_coordinates"]["order_scan_rows"], [0, 12])
        self.assertEqual(raw["executor"]["calibrated_resolution"], [1920, 1080])

    def test_automatic_calibration_scales_all_coordinate_pairs_from_baseline(self):
        raw = {
            "executor": {},
            "coordinate_baseline": {
                "resolution": [1920, 1080],
                "new_order_button": [489, 59],
                "order_form_coordinates": {
                    "symbol": [1160, 370],
                    "pending_type_option_step_y": [0, 14],
                    "order_scan_rows": [0, 12],
                },
            },
        }

        apply_automatic_coordinate_calibration(raw, 1366, 768)

        self.assertEqual(raw["executor"]["new_order_button"], [348, 42])
        self.assertEqual(raw["executor"]["order_form_coordinates"]["symbol"], [825, 263])
        self.assertEqual(raw["executor"]["order_form_coordinates"]["pending_type_option_step_y"], [0, 10])
        self.assertEqual(raw["executor"]["order_form_coordinates"]["order_scan_rows"], [0, 12])

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
