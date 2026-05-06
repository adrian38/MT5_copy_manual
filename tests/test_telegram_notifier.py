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
                }
            }
        )

        self.assertTrue(config.enabled)
        self.assertEqual(config.bot_token, "token")
        self.assertEqual(config.chat_id, "chat")
        self.assertEqual(config.prefix, "PREFIX")

    def test_formats_and_truncates_message(self):
        notifier = TelegramNotifier(
            TelegramConfig(enabled=True, bot_token="token", chat_id="chat", prefix="MT5")
        )

        text = notifier._format_message("x" * 5000)

        self.assertTrue(text.startswith("MT5\n\n"))
        self.assertLessEqual(len(text), 3900)
        self.assertTrue(text.endswith("..."))


if __name__ == "__main__":
    unittest.main()
