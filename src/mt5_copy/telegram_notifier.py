from __future__ import annotations

import json
import queue
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool
    bot_token: str
    chat_id: str
    prefix: str = "MT5 COPY"


class TelegramNotifier:
    def __init__(self, config: TelegramConfig, on_error: Callable[[str], None] | None = None) -> None:
        self.config = config
        self.queue: queue.Queue[str | None] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.last_error: str = ""
        self.on_error = on_error

    def start(self) -> None:
        if not self.config.enabled or not self.config.bot_token or not self.config.chat_id:
            return
        if self.worker and self.worker.is_alive():
            return
        self.worker = threading.Thread(target=self._run, name="telegram-notifier", daemon=True)
        self.worker.start()

    def stop(self) -> None:
        if self.worker and self.worker.is_alive():
            self.queue.put(None)

    def send(self, message: str) -> None:
        if not self.config.enabled or not self.config.bot_token or not self.config.chat_id:
            return
        self.queue.put(message)

    def _run(self) -> None:
        while True:
            message = self.queue.get()
            if message is None:
                return
            try:
                self._send_now(message)
                self.last_error = ""
            except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                self.last_error = str(exc)
                if self.on_error is not None:
                    self.on_error(self.last_error)

    def _send_now(self, message: str) -> None:
        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        text = self._format_message(message)
        payload = json.dumps({"chat_id": self.config.chat_id, "text": text}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()

    def _format_message(self, message: str) -> str:
        text = f"{self.config.prefix}\n\n{message}" if self.config.prefix else message
        if len(text) <= 3900:
            return text
        return text[:3897] + "..."


def telegram_config_from_settings(settings: dict) -> TelegramConfig:
    telegram = dict(settings.get("telegram", {}))
    return TelegramConfig(
        enabled=bool(telegram.get("enabled", False)),
        bot_token=str(telegram.get("bot_token", "")),
        chat_id=str(telegram.get("chat_id", "")),
        prefix=str(telegram.get("prefix", "MT5 COPY")),
    )
