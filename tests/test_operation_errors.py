import csv
import tempfile
import unittest
from pathlib import Path

from src.mt5_copy.executor import PyAutoGuiExecutor
from src.mt5_copy.models import ChangeEvent, ChangeType
from src.mt5_copy.operation_registry import load_operation_errors
from src.mt5_copy.telegram_notifier import TelegramConfig, TelegramNotifier


class FailingCreateGui:
    def __init__(self):
        self.config = type(
            "Config",
            (),
            {
                "order_form_coordinates": {"order_row_anchor": (253, 741), "order_row_step_y": (0, 20)},
                "order_window_delay_seconds": 0,
            },
        )()
        self.calls = 0

    def prepare_pending_order(self, order):
        self.calls += 1
        return {
            "source_ticket": order.get("ticket", ""),
            "symbol": order.get("symbol", ""),
            "type": order.get("type", ""),
            "volume": order.get("volume_current", ""),
            "price_open": order.get("price_open", ""),
            "sl": order.get("sl", ""),
            "tp": order.get("tp", ""),
            "screenshot_order_window": "pending.png",
        }


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


class OperationErrorsTest(unittest.TestCase):
    def test_executor_marks_unverified_create_as_error_after_three_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mapping_file = tmp_path / "mapping.csv"
            destination_orders_file = tmp_path / "destination_orders.csv"
            operation_errors_file = tmp_path / "operation_errors.json"
            _write_mapping(mapping_file)
            _write_orders(destination_orders_file, [])
            notified = []

            gui = FailingCreateGui()
            executor = PyAutoGuiExecutor(
                gui,
                FakeLogger(),
                mapping_file=mapping_file,
                destination_orders_file=destination_orders_file,
                operation_error_file=operation_errors_file,
                on_operation_error=notified.append,
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
                        "price_open": "100",
                        "sl": "90",
                        "tp": "120",
                    },
                    changed_fields={},
                )
            )

            self.assertEqual(gui.calls, 3)
            records = load_operation_errors(operation_errors_file)
            self.assertEqual(len(records), 1)
            record = next(iter(records.values()))
            self.assertEqual(record.operation, "create_order")
            self.assertEqual(record.source_ticket, "1")
            self.assertEqual(record.attempts, 3)
            self.assertEqual(notified, [record])
            with mapping_file.open("r", encoding="utf-8", newline="") as fh:
                mapping_rows = list(csv.DictReader(fh))
            self.assertEqual(mapping_rows[0]["source_ticket"], "1")
            self.assertEqual(mapping_rows[0]["status"], "error")

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
                        "price_open": "100",
                        "sl": "90",
                        "tp": "120",
                    },
                    changed_fields={},
                )
            )
            self.assertEqual(gui.calls, 3)

    def test_telegram_forced_send_ignores_enabled_flag(self):
        sent = []

        class CapturingNotifier(TelegramNotifier):
            def _send_now(self, message):
                sent.append(message)

        notifier = CapturingNotifier(TelegramConfig(enabled=False, bot_token="token", chat_id="chat"))
        notifier.send("normal")
        notifier.send_forced("forced")

        self.assertEqual(sent, ["forced"])


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


def _write_orders(path, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["ticket", "symbol", "type", "volume_current", "price_open", "sl", "tp"],
        )
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
