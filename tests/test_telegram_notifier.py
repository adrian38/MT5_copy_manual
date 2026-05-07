import unittest

from src.mt5_copy.telegram_notifier import (
    TelegramConfig,
    TelegramNotifier,
    telegram_config_from_settings,
)


class TelegramNotifierTest(unittest.TestCase):
    def test_loads_telegram_config_from_settings(self):
        config = telegram_config_from_settings(
            {
                "telegram": {
                    "enabled": True,
                    "bot_token": "token",
                    "chat_id": "chat",
                    "prefix": "PREFIX",
                    "duplicate_window_seconds": 30,
                }
            }
        )

        self.assertTrue(config.enabled)
        self.assertEqual(config.bot_token, "token")
        self.assertEqual(config.chat_id, "chat")
        self.assertEqual(config.prefix, "PREFIX")
        self.assertEqual(config.duplicate_window_seconds, 30)

    def test_formats_and_truncates_message(self):
        notifier = TelegramNotifier(
            TelegramConfig(enabled=True, bot_token="token", chat_id="chat", prefix="MT5")
        )

        text = notifier._format_message("x" * 5000)

        self.assertTrue(text.startswith("MT5\n\n"))
        self.assertLessEqual(len(text), 3900)
        self.assertTrue(text.endswith("..."))

    def test_suppresses_repeated_log_messages_with_different_timestamps(self):
        now = [100.0]
        notifier = TelegramNotifier(
            TelegramConfig(
                enabled=True,
                bot_token="token",
                chat_id="chat",
                prefix="MT5",
                duplicate_window_seconds=60,
            ),
            time_func=lambda: now[0],
        )

        first = "2026-05-07 20:49:01,847 | ERROR | mt5_copy | AUTHORITY_SYNC position close did not remove destination_ticket=99"
        repeated = "2026-05-07 20:49:06,930 | ERROR | mt5_copy | AUTHORITY_SYNC position close did not remove destination_ticket=99"
        later = "2026-05-07 20:50:07,000 | ERROR | mt5_copy | AUTHORITY_SYNC position close did not remove destination_ticket=99"

        notifier.send(first)
        notifier.send(repeated)
        self.assertEqual(notifier.queue.qsize(), 1)

        now[0] = 161.0
        notifier.send(later)
        self.assertEqual(notifier.queue.qsize(), 2)


if __name__ == "__main__":
    unittest.main()
