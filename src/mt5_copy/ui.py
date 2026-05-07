from __future__ import annotations

import logging
import json
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from .app import run_once
from .config import AppConfig, DEFAULT_CONFIG_PATH, load_config
from .csv_reader import read_csv_rows, read_latest_row
from .logging_setup import setup_logging
from .telegram_notifier import TelegramNotifier, telegram_config_from_settings


@dataclass(frozen=True)
class TerminalStatus:
    name: str
    active: bool
    status: str
    heartbeat_age_seconds: float | None
    account_login: str
    account_server: str
    positions_total: str
    orders_total: str
    file_path: Path


class QueueLogHandler(logging.Handler):
    EVENT_PATTERNS = (
        "event=",
        "ORDER_CREATED",
        "ORDER_UPDATED",
        "ORDER_DELETED",
        "POSITION_OPENED",
        "POSITION_UPDATED",
        "POSITION_CLOSED",
        "prepared source_ticket",
        "Modified pending order",
        "Modified position",
        "Reconciling SL/TP",
        "reconciled",
        "Failed to reconcile",
        "GUI READY",
        "Tracking started",
        "Tracking paused",
        "Tracking stop",
        "Terminal route saved",
        "ERROR",
        "WARNING",
        "Exception",
        "Traceback",
    )

    def __init__(self, log_queue: queue.Queue[str]) -> None:
        super().__init__()
        self.log_queue = log_queue
        self._mt5_copy_preserve = True

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        formatted = self.format(record)
        if record.levelno >= logging.WARNING or self._is_event_message(message):
            self.log_queue.put(formatted)

    def _is_event_message(self, message: str) -> bool:
        return any(pattern in message for pattern in self.EVENT_PATTERNS)


def apply_automatic_coordinate_calibration(
    raw: dict[str, Any],
    current_w: int,
    current_h: int,
) -> tuple[int, int, float, float]:
    origin_w, origin_h = 1920, 1080
    executor = raw.setdefault("executor", {})

    if "coordinate_baseline" not in raw:
        raw["coordinate_baseline"] = {
            "resolution": [origin_w, origin_h],
            "order_form_coordinates": executor.get("order_form_coordinates", {}),
            "new_order_button": executor.get("new_order_button"),
        }

    baseline = raw["coordinate_baseline"]
    base_w, base_h = (int(part) for part in baseline.get("resolution", [origin_w, origin_h]))
    if base_w <= 0 or base_h <= 0:
        base_w, base_h = origin_w, origin_h
        baseline["resolution"] = [base_w, base_h]

    scale_x = current_w / base_w
    scale_y = current_h / base_h

    non_coord = {"order_scan_rows"}
    new_coords: dict[str, Any] = {}
    for key, val in dict(baseline.get("order_form_coordinates", {})).items():
        if key in non_coord or not isinstance(val, list) or len(val) != 2:
            new_coords[key] = val
        else:
            new_coords[key] = [round(float(val[0]) * scale_x), round(float(val[1]) * scale_y)]

    executor["order_form_coordinates"] = new_coords

    base_btn = baseline.get("new_order_button")
    if isinstance(base_btn, list) and len(base_btn) == 2:
        executor["new_order_button"] = [
            round(float(base_btn[0]) * scale_x),
            round(float(base_btn[1]) * scale_y),
        ]

    executor["calibrated_resolution"] = [current_w, current_h]
    return base_w, base_h, scale_x, scale_y


class CopyMonitorApp:
    def __init__(self, root: tk.Tk, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
        self.root = root
        self.config_path = config_path
        self.config = load_config(config_path)
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.last_scan_events = 0
        self.last_scan_at = "-"
        self.scan_error = ""
        self.telegram_error_queue: queue.Queue[str] = queue.Queue()
        self.telegram = TelegramNotifier(
            telegram_config_from_settings(self.config.notifications),
            on_error=self.telegram_error_queue.put,
        )
        self.telegram.start()

        self.logger = setup_logging(self.config.app_log_file, self.config.log_level)
        self._attach_queue_logger()

        self.root.title("MT5 Copy Manual")
        self.root.geometry("980x680")
        self.root.minsize(880, 600)

        self.mode_var = tk.StringVar(value="PAUSADO")
        self.master_state_var = tk.StringVar(value="-")
        self.destination_state_var = tk.StringVar(value="-")
        self.last_scan_var = tk.StringVar(value="-")
        self.last_events_var = tk.StringVar(value="0")
        self.poll_seconds_var = tk.StringVar(value=f"{self.config.poll_seconds:.1f}")
        self.error_var = tk.StringVar(value="")
        self.executor_var = tk.StringVar(value=self._executor_text())
        self.source_terminal_var = tk.StringVar(value=str(self.config.terminals.get("source_terminal_id", "")))
        self.destination_terminal_var = tk.StringVar(
            value=str(self.config.terminals.get("destination_terminal_id", ""))
        )
        self.source_prefix_var = tk.StringVar(value=self._selected_terminal_value("source_terminal_id", "file_prefix"))
        self.destination_prefix_var = tk.StringVar(
            value=self._selected_terminal_value("destination_terminal_id", "file_prefix")
        )
        self.destination_window_var = tk.StringVar(
            value=str(self.config.executor.get("mt5_window_title_contains", ""))
        )
        self.common_files_var = tk.StringVar(value=str(self.config.common_files_path))

        self._install_tk_exception_handler()
        self._build_ui()
        self._safe_ui_call(self._write_startup_summary)
        self._safe_ui_call(self._refresh_status)
        self._drain_logs()
        self._drain_telegram_errors()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            if self.stop_event.is_set():
                self._set_mode("DETENIENDO")
                self.logger.warning("Tracking start ignored because stop is still in progress.")
                return
            self.pause_event.clear()
            self._set_mode("ACTIVO")
            self.logger.info("Tracking resumed from UI.")
            return

        self.stop_event.clear()
        self.pause_event.clear()
        self.worker = threading.Thread(target=self._worker_loop, name="copy-monitor", daemon=True)
        self.worker.start()
        self._set_mode("ACTIVO")
        self.logger.info("Tracking started from UI.")

    def pause(self) -> None:
        if self.stop_event.is_set():
            self._set_mode("DETENIENDO")
            return
        self.pause_event.set()
        self._set_mode("PAUSADO")
        self.logger.info("Tracking paused from UI.")

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.set()
        if self.worker and self.worker.is_alive():
            self._set_mode("DETENIENDO")
        else:
            self._set_mode("DETENIDO")
        self.logger.info("Tracking stop requested from UI.")

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(4, weight=1)

        header = ttk.Frame(self.root, padding=(16, 14, 16, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="MT5 Copy Manual", font=("Segoe UI", 18, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, textvariable=self.mode_var, font=("Segoe UI", 12, "bold")).grid(
            row=0, column=1, sticky="e"
        )
        ttk.Label(header, textvariable=self.executor_var).grid(row=1, column=0, columnspan=2, sticky="w")

        controls = ttk.Frame(self.root, padding=(16, 0, 16, 10))
        controls.grid(row=1, column=0, sticky="ew")
        ttk.Button(controls, text="Iniciar seguimiento", command=lambda: self._safe_ui_call(self.start)).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(controls, text="Pausar", command=lambda: self._safe_ui_call(self.pause)).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(controls, text="Detener", command=lambda: self._safe_ui_call(self.stop)).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(controls, text="Actualizar estado", command=lambda: self._safe_ui_call(self._refresh_status)).grid(row=0, column=3)
        ttk.Button(controls, text="Guardar terminales", command=lambda: self._safe_ui_call(self._save_terminal_settings)).grid(
            row=0, column=4, padx=(8, 0)
        )
        ttk.Button(controls, text="Calibrar pantalla", command=lambda: self._safe_ui_call(self._calibrate_coordinates)).grid(
            row=0, column=5, padx=(8, 0)
        )
        self.telegram_btn = ttk.Button(
            controls, text=self._telegram_btn_text(),
            command=lambda: self._safe_ui_call(self._toggle_telegram),
        )
        self.telegram_btn.grid(row=0, column=6, padx=(8, 0))

        routing = ttk.LabelFrame(self.root, text="Ruta de copia", padding=10)
        routing.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 10))
        routing.columnconfigure(1, weight=1)
        routing.columnconfigure(3, weight=1)

        terminal_ids = self._terminal_ids()
        ttk.Label(routing, text="Copiar desde").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.source_combo = ttk.Combobox(
            routing,
            textvariable=self.source_terminal_var,
            values=terminal_ids,
        )
        self.source_combo.grid(row=0, column=1, sticky="ew", padx=(0, 14))
        ttk.Label(routing, text="Prefijo CSV").grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Entry(routing, textvariable=self.source_prefix_var).grid(row=0, column=3, sticky="ew")

        ttk.Label(routing, text="Copiar hacia").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(8, 0))
        self.destination_combo = ttk.Combobox(
            routing,
            textvariable=self.destination_terminal_var,
            values=terminal_ids,
        )
        self.destination_combo.grid(row=1, column=1, sticky="ew", padx=(0, 14), pady=(8, 0))
        ttk.Label(routing, text="ID ventana destino").grid(row=1, column=2, sticky="w", padx=(0, 6), pady=(8, 0))
        ttk.Entry(routing, textvariable=self.destination_window_var).grid(row=1, column=3, sticky="ew", pady=(8, 0))

        ttk.Label(routing, text="Prefijo destino").grid(row=2, column=2, sticky="w", padx=(0, 6), pady=(8, 0))
        ttk.Entry(routing, textvariable=self.destination_prefix_var).grid(row=2, column=3, sticky="ew", pady=(8, 0))

        ttk.Label(routing, text="Common Files MT5").grid(row=3, column=0, sticky="w", padx=(0, 6), pady=(8, 0))
        ttk.Entry(routing, textvariable=self.common_files_var).grid(
            row=3, column=1, columnspan=3, sticky="ew", pady=(8, 0)
        )

        self.source_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_terminal_fields())
        self.destination_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_terminal_fields())

        status_frame = ttk.Frame(self.root, padding=(16, 0, 16, 10))
        status_frame.grid(row=3, column=0, sticky="ew")
        status_frame.columnconfigure(0, weight=1)
        status_frame.columnconfigure(1, weight=1)

        self._terminal_card(status_frame, "Master / Origen", self.master_state_var, 0)
        self._terminal_card(status_frame, "Destino / Copia", self.destination_state_var, 1)

        body = ttk.Frame(self.root, padding=(16, 0, 16, 16))
        body.grid(row=4, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        summary = ttk.Frame(body)
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(summary, text="Ultimo scan:").grid(row=0, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.last_scan_var).grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(summary, text="Eventos detectados:").grid(row=0, column=2, sticky="w")
        ttk.Label(summary, textvariable=self.last_events_var).grid(row=0, column=3, sticky="w", padx=(6, 18))
        ttk.Label(summary, text="Periodicidad:").grid(row=0, column=4, sticky="w")
        poll_entry = ttk.Entry(summary, textvariable=self.poll_seconds_var, width=7)
        poll_entry.grid(row=0, column=5, sticky="w", padx=(6, 2))
        poll_entry.bind("<Return>", lambda _e: self._safe_ui_call(self._save_poll_seconds))
        poll_entry.bind("<FocusOut>", lambda _e: self._safe_ui_call(self._save_poll_seconds))
        ttk.Label(summary, text="s").grid(row=0, column=6, sticky="w", padx=(0, 18))
        ttk.Label(summary, textvariable=self.error_var, foreground="#9a3412").grid(
            row=0, column=7, sticky="w"
        )

        log_frame = ttk.LabelFrame(body, text="Actividad de cambios", padding=8)
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=18, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _terminal_card(
        self,
        parent: ttk.Frame,
        title: str,
        text_variable: tk.StringVar,
        column: int,
    ) -> None:
        card = ttk.LabelFrame(parent, text=title, padding=10)
        card.grid(row=0, column=column, sticky="ew", padx=(0, 8) if column == 0 else (8, 0))
        card.columnconfigure(0, weight=1)
        ttk.Label(card, textvariable=text_variable, justify="left").grid(row=0, column=0, sticky="ew")

    def _worker_loop(self) -> None:
        while not self.stop_event.is_set():
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.25)
            if self.stop_event.is_set():
                break

            try:
                events = run_once(self.config_path)
                self.last_scan_events = events
                self.last_scan_at = time.strftime("%Y-%m-%d %H:%M:%S")
                self.scan_error = ""
            except Exception as exc:
                self.scan_error = str(exc)
                self.logger.exception("UI tracking scan failed.")

            sleep_until = time.monotonic() + self.config.poll_seconds
            while time.monotonic() < sleep_until and not self.stop_event.is_set():
                if self.pause_event.is_set():
                    break
                time.sleep(0.1)

        self._set_mode("DETENIDO")

    def _set_mode(self, value: str) -> None:
        if threading.current_thread() is threading.main_thread():
            self.mode_var.set(value)
        else:
            self.root.after(0, self.mode_var.set, value)

    def _refresh_status(self) -> None:
        try:
            self.config = load_config(self.config_path)
        except Exception as exc:
            self.scan_error = f"Config error: {exc}"
            self.error_var.set(self.scan_error)
            self._append_log(self.scan_error)
            self.root.after(1000, lambda: self._safe_ui_call(self._refresh_status))
            return

        master = read_terminal_status(
            "Master",
            self.config.heartbeat_file,
            stale_after_seconds=max(self.config.poll_seconds * 4, 8.0),
        )
        destination = read_terminal_status(
            "Destino",
            self.config.destination_heartbeat_file,
            stale_after_seconds=max(self.config.poll_seconds * 4, 8.0),
        )

        self.master_state_var.set(self._status_text(master))
        self.destination_state_var.set(self._status_text(destination))
        self.last_scan_var.set(self.last_scan_at)
        self.last_events_var.set(str(self.last_scan_events))
        self.error_var.set(self.scan_error)
        self.executor_var.set(self._executor_text())
        self.telegram_btn.config(text=self._telegram_btn_text())

        self.root.after(1000, lambda: self._safe_ui_call(self._refresh_status))

    def _load_terminal_fields(self) -> None:
        self.source_prefix_var.set(self._terminal_value(self.source_terminal_var.get(), "file_prefix"))
        self.destination_prefix_var.set(self._terminal_value(self.destination_terminal_var.get(), "file_prefix"))
        self.destination_window_var.set(
            self._terminal_value(self.destination_terminal_var.get(), "window_title_contains")
        )

    def _save_terminal_settings(self) -> None:
        with self.config_path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)

        terminals = raw.setdefault("terminals", {})
        terminals["source_terminal_id"] = self.source_terminal_var.get().strip()
        terminals["destination_terminal_id"] = self.destination_terminal_var.get().strip()
        items = terminals.setdefault("items", [])

        self._upsert_terminal(
            items,
            terminal_id=terminals["source_terminal_id"],
            role="source",
            file_prefix=self.source_prefix_var.get().strip() or "slave",
            window_title_contains=self._terminal_value(terminals["source_terminal_id"], "window_title_contains"),
        )
        self._upsert_terminal(
            items,
            terminal_id=terminals["destination_terminal_id"],
            role="destination",
            file_prefix=self.destination_prefix_var.get().strip() or "destination",
            window_title_contains=self.destination_window_var.get().strip(),
        )

        executor = raw.setdefault("executor", {})
        executor["mt5_window_title_contains"] = self.destination_window_var.get().strip()

        common_files = self.common_files_var.get().strip()
        if common_files:
            raw["common_files_path"] = common_files

        with self.config_path.open("w", encoding="utf-8") as fh:
            json.dump(raw, fh, indent=2)
            fh.write("\n")

        self.config = load_config(self.config_path)
        self.executor_var.set(self._executor_text())
        self.logger.info(
            "Terminal route saved: source=%s destination=%s window_id=%s common_files=%s",
            terminals["source_terminal_id"],
            terminals["destination_terminal_id"],
            executor["mt5_window_title_contains"],
            common_files,
        )
        self._refresh_status()

    @staticmethod
    def _upsert_terminal(
        items: list[dict[str, Any]],
        terminal_id: str,
        role: str,
        file_prefix: str,
        window_title_contains: str,
    ) -> None:
        for item in items:
            if str(item.get("id", "")) == terminal_id:
                item["role"] = role
                item["file_prefix"] = file_prefix
                item["window_title_contains"] = window_title_contains
                item["enabled"] = True
                return

        items.append(
            {
                "id": terminal_id,
                "label": terminal_id,
                "role": role,
                "file_prefix": file_prefix,
                "window_title_contains": window_title_contains,
                "enabled": True,
            }
        )

    def _telegram_btn_text(self) -> str:
        enabled = self.config.notifications.get("telegram", {}).get("enabled", False)
        return "Telegram: ON" if enabled else "Telegram: OFF"

    def _toggle_telegram(self) -> None:
        with self.config_path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)

        telegram = raw.setdefault("notifications", {}).setdefault("telegram", {})
        new_state = not bool(telegram.get("enabled", False))
        telegram["enabled"] = new_state

        with self.config_path.open("w", encoding="utf-8") as fh:
            json.dump(raw, fh, indent=2)
            fh.write("\n")

        self.config = load_config(self.config_path)

        self.telegram.stop()
        self.telegram = TelegramNotifier(
            telegram_config_from_settings(self.config.notifications),
            on_error=self.telegram_error_queue.put,
        )
        self.telegram.start()

        self.telegram_btn.config(text=self._telegram_btn_text())
        estado = "activadas" if new_state else "desactivadas"
        self.logger.info("Notificaciones Telegram %s", estado)

    def _calibrate_coordinates(self) -> None:
        try:
            import pyautogui as _pag
            current_w, current_h = _pag.size()
        except Exception:
            current_w = self.root.winfo_screenwidth()
            current_h = self.root.winfo_screenheight()

        with self.config_path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)

        base_w, base_h, scale_x, scale_y = apply_automatic_coordinate_calibration(
            raw,
            int(current_w),
            int(current_h),
        )
        executor = raw.setdefault("executor", {})
        baseline = raw["coordinate_baseline"]

        if base_w == current_w and base_h == current_h:
            # Save the rebuilt executor coordinates even when the scale is 1:1.
            with self.config_path.open("w", encoding="utf-8") as fh:
                json.dump(raw, fh, indent=2)
                fh.write("\n")
            messagebox.showinfo(
                "Calibrar pantalla",
                f"Las coordenadas ya están calibradas para {current_w}x{current_h}.",
            )
            return

        scale_x = current_w / base_w
        scale_y = current_h / base_h
        if not messagebox.askyesno(
            "Calibrar pantalla",
            f"Pantalla detectada: {current_w}x{current_h}\n"
            f"Baseline: {base_w}x{base_h}\n"
            f"Factor X: {scale_x:.4f}  |  Factor Y: {scale_y:.4f}\n\n"
            "¿Aplicar reescalado a todas las coordenadas?",
        ):
            return

        _NON_COORD = {"order_scan_rows"}
        new_coords: dict[str, Any] = {}
        for key, val in baseline.get("order_form_coordinates", {}).items():
            if key in _NON_COORD or not isinstance(val, list) or len(val) != 2:
                new_coords[key] = val
            else:
                new_coords[key] = [round(val[0] * scale_x), round(val[1] * scale_y)]

        executor["order_form_coordinates"] = new_coords

        base_btn = baseline.get("new_order_button")
        if isinstance(base_btn, list) and len(base_btn) == 2:
            executor["new_order_button"] = [round(base_btn[0] * scale_x), round(base_btn[1] * scale_y)]

        with self.config_path.open("w", encoding="utf-8") as fh:
            json.dump(raw, fh, indent=2)
            fh.write("\n")

        self.config = load_config(self.config_path)
        self.logger.info(
            "Coordinates calibrated %sx%s → %sx%s scale_x=%.4f scale_y=%.4f",
            base_w, base_h, current_w, current_h, scale_x, scale_y,
        )
        messagebox.showinfo(
            "Calibrar pantalla",
            f"Coordenadas actualizadas para {current_w}x{current_h}.",
        )

    def _save_poll_seconds(self) -> None:
        try:
            value = float(self.poll_seconds_var.get().replace(",", "."))
        except ValueError:
            self.poll_seconds_var.set(f"{self.config.poll_seconds:.1f}")
            return
        if value <= 0:
            self.poll_seconds_var.set(f"{self.config.poll_seconds:.1f}")
            return

        with self.config_path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        raw["poll_seconds"] = value
        with self.config_path.open("w", encoding="utf-8") as fh:
            json.dump(raw, fh, indent=2)
            fh.write("\n")

        self.config = load_config(self.config_path)
        self.poll_seconds_var.set(f"{self.config.poll_seconds:.1f}")
        self.logger.info("poll_seconds updated to %s", value)

    def _drain_logs(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(message)
        self.root.after(250, self._drain_logs)

    def _drain_telegram_errors(self) -> None:
        while True:
            try:
                message = self.telegram_error_queue.get_nowait()
            except queue.Empty:
                break
            self.scan_error = f"Telegram error: {message}"
            self.error_var.set(self.scan_error)
            self._append_log(self.scan_error, notify=False)
            self.logger.error(self.scan_error)
        self.root.after(1000, self._drain_telegram_errors)

    def _append_log(self, message: str, notify: bool = True) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 400:
            self.log_text.delete("1.0", f"{line_count - 400}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        if notify:
            self.telegram.send(message)

    def _write_startup_summary(self) -> None:
        general_lines = [
            "UI iniciada. Seguimiento en pausa hasta pulsar 'Iniciar seguimiento'.",
            "Ruta actual: "
            f"{self.source_terminal_var.get()}[{self.source_prefix_var.get()}] -> "
            f"{self.destination_terminal_var.get()}[{self.destination_prefix_var.get()}]",
            f"Ventana destino configurada: {self.destination_window_var.get()}",
            f"Common Files: {self.config.common_files_path}",
        ]
        if self.telegram.config.enabled:
            general_lines.append("Telegram: notificaciones activas")
        else:
            general_lines.append("Telegram: notificaciones desactivadas")

        self._append_and_send_block(general_lines)
        self._append_and_send_block(
            self._trade_snapshot_lines("Origen posiciones", self.config.positions_file, is_position=True)
        )
        self._append_and_send_block(
            self._trade_snapshot_lines("Origen ordenes", self.config.orders_file, is_position=False)
        )
        self._append_and_send_block(
            self._trade_snapshot_lines(
                "Destino posiciones",
                self.config.destination_positions_file,
                is_position=True,
            )
        )
        self._append_and_send_block(
            self._trade_snapshot_lines(
                "Destino ordenes",
                self.config.destination_orders_file,
                is_position=False,
            )
        )

    def _append_and_send_block(self, lines: list[str]) -> None:
        for line in lines:
            self._append_log(line, notify=False)
        self.telegram.send("\n".join(lines))

    def _trade_snapshot_lines(self, title: str, path: Path, is_position: bool) -> list[str]:
        rows = read_csv_rows(path)
        lines = [f"{title}: {len(rows)}"]
        for row in rows[:20]:
            volume = row.get("volume") if is_position else row.get("volume_current", row.get("volume_initial", ""))
            lines.append(
                "  "
                f"ticket={row.get('ticket', '')} "
                f"symbol={row.get('symbol', '')} "
                f"type={row.get('type', '')} "
                f"vol={volume} "
                f"price={row.get('price_open', '')} "
                f"sl={row.get('sl', '')} "
                f"tp={row.get('tp', '')}"
            )
        if len(rows) > 20:
            lines.append(f"  ... {len(rows) - 20} mas")
        return lines

    def _write_trade_snapshot(self, title: str, path: Path, is_position: bool) -> None:
        self._append_and_send_block(self._trade_snapshot_lines(title, path, is_position))

    def _attach_queue_logger(self) -> None:
        if any(isinstance(handler, QueueLogHandler) for handler in self.logger.handlers):
            return
        handler = QueueLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        self.logger.addHandler(handler)

    def _executor_text(self) -> str:
        executor = self.config.executor
        mode = executor.get("mode", "dry_run")
        armed = executor.get("armed_for_trading", False)
        submit_orders = executor.get("submit_orders", False)
        target = executor.get("mt5_window_title_contains", "MetaTrader")
        return f"Executor: {mode} | armado={armed} | submit_orders={submit_orders} | destino='{target}'"

    def _terminal_ids(self) -> list[str]:
        items = self.config.terminals.get("items", [])
        if not isinstance(items, list):
            return []
        return [str(item.get("id", "")) for item in items if isinstance(item, dict) and item.get("id")]

    def _selected_terminal_value(self, selected_key: str, value_key: str) -> str:
        selected_id = str(self.config.terminals.get(selected_key, ""))
        return self._terminal_value(selected_id, value_key)

    def _terminal_value(self, terminal_id: str, value_key: str) -> str:
        items = self.config.terminals.get("items", [])
        if not isinstance(items, list):
            return ""
        for item in items:
            if isinstance(item, dict) and str(item.get("id", "")) == str(terminal_id):
                return str(item.get(value_key, ""))
        return ""

    @staticmethod
    def _status_text(status: TerminalStatus) -> str:
        marker = "ACTIVO" if status.active else "SIN SENAL"
        age = "-" if status.heartbeat_age_seconds is None else f"{status.heartbeat_age_seconds:.1f}s"
        return (
            f"Estado: {marker}\n"
            f"Heartbeat: {status.status} | edad={age}\n"
            f"Cuenta: {status.account_login} | {status.account_server}\n"
            f"Posiciones: {status.positions_total} | Ordenes: {status.orders_total}\n"
            f"Archivo: {status.file_path}"
        )

    def _on_close(self) -> None:
        self.stop()
        self.telegram.stop()
        self.root.after(250, self.root.destroy)

    def _install_tk_exception_handler(self) -> None:
        def report_callback_exception(exc_type, exc_value, exc_traceback) -> None:
            self.scan_error = f"UI error: {exc_value}"
            try:
                self.error_var.set(self.scan_error)
                self._append_log(self.scan_error)
            except Exception:
                pass
            self.logger.error("UI callback failed", exc_info=(exc_type, exc_value, exc_traceback))

        self.root.report_callback_exception = report_callback_exception

    def _safe_ui_call(self, callback, *args, **kwargs) -> None:
        try:
            callback(*args, **kwargs)
        except Exception as exc:
            self.scan_error = f"UI error: {exc}"
            try:
                self.error_var.set(self.scan_error)
                self._append_log(self.scan_error)
            finally:
                self.logger.exception("UI action failed.")


def read_terminal_status(
    name: str,
    heartbeat_file: Path,
    stale_after_seconds: float,
) -> TerminalStatus:
    try:
        heartbeat = read_latest_row(heartbeat_file) or {}
    except PermissionError:
        heartbeat = {"status": "FILE_LOCKED"}
    age = _file_age_seconds(heartbeat_file)
    active = (
        bool(heartbeat)
        and heartbeat.get("status") == "RUNNING"
        and age is not None
        and age <= stale_after_seconds
    )
    return TerminalStatus(
        name=name,
        active=active,
        status=str(heartbeat.get("status", "NO_HEARTBEAT")),
        heartbeat_age_seconds=age,
        account_login=str(heartbeat.get("account_login", "-")),
        account_server=str(heartbeat.get("account_server", "-")),
        positions_total=str(heartbeat.get("positions_total", "-")),
        orders_total=str(heartbeat.get("orders_total", "-")),
        file_path=heartbeat_file,
    )


def _file_age_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def run_ui(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    root = tk.Tk()
    CopyMonitorApp(root, config_path)
    root.mainloop()
