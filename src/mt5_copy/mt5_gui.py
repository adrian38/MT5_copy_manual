from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from statistics import median


class GuiDependencyError(RuntimeError):
    pass


class GuiSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class GuiConfig:
    window_title_contains: str
    screenshot_dir: Path
    image_dir: Path
    image_confidence: float
    action_pause_seconds: float
    fail_safe: bool
    armed_for_trading: bool
    submit_orders: bool
    new_order_hotkey: tuple[str, ...]
    new_order_button: tuple[int, int] | None
    order_dialog_title_contains: str
    order_window_delay_seconds: float
    field_delay_seconds: float
    comment_prefix: str
    order_form_coordinates: dict[str, tuple[int, int]]
    order_search_scroll_pages: int = 4
    order_search_scroll_clicks: int = 8
    order_search_arrow_down_presses: int = 8


class Mt5GuiController:
    def __init__(self, config: GuiConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.pyautogui = self._load_pyautogui()
        self.pyautogui.PAUSE = config.action_pause_seconds
        self.pyautogui.FAILSAFE = config.fail_safe

    def check_environment(self) -> dict[str, Any]:
        screen_size = self.pyautogui.size()
        windows = self._matching_windows()
        return {
            "screen_size": {"width": screen_size.width, "height": screen_size.height},
            "window_title_contains": self.config.window_title_contains,
            "matching_windows": [getattr(window, "title", "") for window in windows],
            "armed_for_trading": self.config.armed_for_trading,
        }

    def list_windows(self) -> list[dict[str, Any]]:
        windows = []
        try:
            all_windows = self.pyautogui.getAllWindows()
        except Exception:
            self.logger.exception("Could not list windows.")
            return windows

        for window in all_windows:
            title = getattr(window, "title", "")
            if not title:
                continue
            windows.append(
                {
                    "title": title,
                    "left": getattr(window, "left", None),
                    "top": getattr(window, "top", None),
                    "width": getattr(window, "width", None),
                    "height": getattr(window, "height", None),
                    "is_minimized": getattr(window, "isMinimized", None),
                }
            )
        return windows

    def focus_mt5(self) -> bool:
        windows = self._matching_windows()
        if not windows:
            self.logger.warning(
                "No MT5 window found containing title text: %s",
                self.config.window_title_contains,
            )
            return False

        window = windows[0]
        title = getattr(window, "title", "")
        try:
            if getattr(window, "isMinimized", False):
                window.restore()
            window.activate()
            self.logger.info("Focused MT5 window: %s", title)
            return True
        except Exception:
            self.logger.warning("Window activate failed, trying click fallback: %s", title)
            try:
                x = int(getattr(window, "left", 0) + (getattr(window, "width", 0) / 2))
                y = int(getattr(window, "top", 0) + 20)
                self.pyautogui.click(x, y)
                self.logger.info("Focused MT5 window by click fallback: %s", title)
                return True
            except Exception:
                self.logger.exception("Could not focus MT5 window: %s", title)
                return False

    def screenshot(self, name: str = "mt5") -> Path:
        self.config.screenshot_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.config.screenshot_dir / f"{name}_{timestamp}.png"
        image = self.pyautogui.screenshot()
        image.save(path)
        self.logger.info("Saved screenshot: %s", path)
        return path

    def locate_image(self, image_name: str):
        image_path = self.config.image_dir / image_name
        if not image_path.exists():
            raise FileNotFoundError(f"Image reference not found: {image_path}")
        return self.pyautogui.locateOnScreen(
            str(image_path),
            confidence=self.config.image_confidence,
        )

    def calibrate_toolbox_coordinates(
        self,
        destination_positions: list[dict[str, Any]],
        destination_orders: list[dict[str, Any]],
    ) -> dict[str, tuple[int, int]]:
        self.logger.info("Focusing MT5 target before toolbox calibration.")
        focused = self.focus_mt5()
        if not focused:
            raise GuiSafetyError("Cannot calibrate toolbox because MT5 target was not focused.")

        tickets = [
            str(row.get("ticket", "")).strip()
            for row in [*destination_positions, *destination_orders]
            if str(row.get("ticket", "")).strip()
        ]
        ticket_centers = self._locate_ticket_centers(tickets) if tickets else {}

        updates: dict[str, tuple[int, int]] = {}
        updates.update(self._calibrate_row_group("position", destination_positions, ticket_centers))
        updates.update(self._calibrate_row_group("order", destination_orders, ticket_centers))
        if not updates:
            updates = self._fallback_toolbox_coordinates()
            self.logger.warning(
                "Toolbox calibration used fallback geometry because no destination tickets were available/readable."
            )

        self.config.order_form_coordinates.update(updates)
        self.logger.info("Toolbox coordinates calibrated: %s", updates)
        return updates

    def click_image(self, image_name: str) -> bool:
        self._require_armed("click image")
        location = self.locate_image(image_name)
        if location is None:
            self.logger.warning("Image not found on screen: %s", image_name)
            return False
        center = self.pyautogui.center(location)
        self.pyautogui.click(center.x, center.y)
        self.logger.info("Clicked image %s at x=%s y=%s", image_name, center.x, center.y)
        return True

    def hotkey(self, *keys: str) -> None:
        self._require_armed(f"hotkey {keys}")
        self.pyautogui.hotkey(*keys)
        self.logger.info("Pressed hotkey: %s", "+".join(keys))

    def press(self, key: str) -> None:
        self._require_armed(f"press {key}")
        self.pyautogui.press(key)
        self.logger.info("Pressed key: %s", key)

    def type_text(self, text: str) -> None:
        self._require_armed("type text")
        self.pyautogui.write(text)
        self.logger.info("Typed text length=%s", len(text))

    def paste_text(self, text: str) -> None:
        self._require_armed("paste text")
        try:
            import pyperclip
        except ImportError as exc:
            raise GuiDependencyError(
                "pyperclip is not installed. Run: py -m pip install -r requirements.txt"
            ) from exc

        pyperclip.copy(text)
        self.pyautogui.hotkey("ctrl", "v")
        self.logger.info("Pasted text length=%s", len(text))

    def open_new_order_window(self) -> Path:
        self._require_armed("open new order window")
        focused = self.focus_mt5()
        if not focused:
            raise GuiSafetyError("Cannot open order window because MT5 target was not focused.")

        if len(self.config.new_order_hotkey) == 1:
            self.pyautogui.press(self.config.new_order_hotkey[0])
        else:
            self.pyautogui.hotkey(*self.config.new_order_hotkey)

        time.sleep(self.config.order_window_delay_seconds)
        if not self._order_dialog_is_open() and self.config.new_order_button is not None:
            self.logger.warning("New order hotkey did not open dialog. Trying toolbar button.")
            self.pyautogui.click(*self.config.new_order_button)
            time.sleep(self.config.order_window_delay_seconds)

        if not self._order_dialog_is_open():
            screenshot_path = self.screenshot("new_order_not_open")
            raise GuiSafetyError(
                f"New order dialog did not open. Screenshot: {screenshot_path}"
            )

        screenshot_path = self.screenshot("new_order_window")
        self.logger.info("Opened MT5 new order window: %s", screenshot_path)
        return screenshot_path

    def prepare_market_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self.close_active_dialog()
        screenshot_before = self.screenshot("before_market_order")
        order_window = self.open_new_order_window()
        # MT5 remembers the last mode used — explicitly switch to market execution.
        self.switch_to_market_execution_mode()
        symbol_screenshot = self.select_symbol(str(order.get("symbol", "")))
        fields_screenshot = self.fill_market_order_fields(order)
        prepared = {
            "source_ticket": order.get("ticket", ""),
            "symbol": order.get("symbol", ""),
            "type": order.get("type", ""),
            "volume": order.get("volume_current", order.get("volume_initial", "")),
            "sl": order.get("sl", ""),
            "tp": order.get("tp", ""),
            "screenshot_before": str(screenshot_before),
            "screenshot_order_window": str(order_window),
            "screenshot_symbol_selected": str(symbol_screenshot),
            "screenshot_fields_filled": str(fields_screenshot),
            "submit_orders": self.config.submit_orders,
        }

        if not self.config.submit_orders:
            self.logger.warning(
                "Order submit is disabled. Prepared market order only: %s",
                prepared,
            )
            return prepared

        submit_screenshot = self.submit_market_order(str(order.get("type", "")))
        prepared["screenshot_submitted"] = str(submit_screenshot)
        return prepared

    def switch_to_market_execution_mode(self) -> None:
        self._require_armed("switch to market execution mode")
        coordinates = self.config.order_form_coordinates
        if "execution_type" not in coordinates:
            raise GuiSafetyError("Missing coordinates for order field: execution_type")
        x, y = coordinates["execution_type"]
        self.pyautogui.click(x, y)
        time.sleep(self.config.field_delay_seconds)
        # Home key moves to the first item (Market Execution) in the dropdown.
        self.pyautogui.hotkey("home")
        time.sleep(self.config.field_delay_seconds)
        self.pyautogui.press("enter")
        time.sleep(self.config.order_window_delay_seconds)
        self.logger.info("Switched order window to market execution mode.")

    def fill_market_order_fields(self, order: dict[str, Any]) -> Path:
        self._require_armed("fill market order fields")
        coordinates = self.config.order_form_coordinates
        values = {
            "volume": _format_number(order.get("volume_current", order.get("volume_initial", ""))),
            "sl": _format_number(order.get("sl", "")),
            "tp": _format_number(order.get("tp", "")),
        }
        for field_name, value in values.items():
            self._replace_field_text(field_name, value, coordinates)
        screenshot_path = self.screenshot("market_order_fields_filled")
        self.logger.info(
            "Filled market order fields: type=%s values=%s",
            order.get("type", ""),
            values,
        )
        return screenshot_path

    def submit_market_order(self, order_type: str) -> Path:
        self._require_armed("submit market order")
        coordinates = self.config.order_form_coordinates
        order_type_upper = order_type.strip().upper()
        if order_type_upper == "BUY":
            button_key = "buy_market"
        elif order_type_upper == "SELL":
            button_key = "sell_market"
        else:
            raise GuiSafetyError(f"Unsupported market order type: {order_type}")
        if button_key not in coordinates:
            raise GuiSafetyError(
                f"Missing coordinates for market order button: {button_key}. "
                "Add 'buy_market' and 'sell_market' to order_form_coordinates in settings.json."
            )
        x, y = coordinates[button_key]
        self.pyautogui.click(x, y)
        time.sleep(self.config.order_window_delay_seconds)
        screenshot_path = self.screenshot("after_market_order_clicked")
        self.logger.info("Clicked market order %s button: %s", order_type, screenshot_path)
        return screenshot_path

    def prepare_pending_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self.close_active_dialog()
        screenshot_before = self.screenshot("before_order_created")
        order_window = self.open_new_order_window()
        symbol_screenshot = self.select_symbol(str(order.get("symbol", "")))
        pending_mode_screenshot = self.switch_to_pending_order_mode()
        fields_screenshot = self.fill_basic_order_fields(order)
        prepared = {
            "source_ticket": order.get("ticket", ""),
            "symbol": order.get("symbol", ""),
            "type": order.get("type", ""),
            "volume": order.get("volume_current", order.get("volume_initial", "")),
            "price_open": order.get("price_open", ""),
            "sl": order.get("sl", ""),
            "tp": order.get("tp", ""),
            "time_expiration": order.get("time_expiration", ""),
            "screenshot_before": str(screenshot_before),
            "screenshot_order_window": str(order_window),
            "screenshot_symbol_selected": str(symbol_screenshot),
            "screenshot_pending_mode": str(pending_mode_screenshot),
            "screenshot_fields_filled": str(fields_screenshot),
            "submit_orders": self.config.submit_orders,
        }

        if not self.config.submit_orders:
            self.logger.warning(
                "Order submit is disabled. Prepared pending order only: %s",
                prepared,
            )
            return prepared

        submit_screenshot = self.submit_pending_order()
        prepared["screenshot_submitted"] = str(submit_screenshot)
        return prepared

    def prepare_market_position(self, position: dict[str, Any]) -> dict[str, Any]:
        self.close_active_dialog()
        screenshot_before = self.screenshot("before_position_opened")
        order_window = self.open_new_order_window()
        symbol_screenshot = self.select_symbol(str(position.get("symbol", "")))
        self.switch_to_market_execution_mode()
        market_mode_screenshot = self.screenshot("market_order_mode")
        fields_screenshot = self.fill_market_position_fields(position)
        prepared = {
            "source_ticket": position.get("ticket", ""),
            "symbol": position.get("symbol", ""),
            "type": position.get("type", ""),
            "volume": position.get("volume", ""),
            "sl": position.get("sl", ""),
            "tp": position.get("tp", ""),
            "screenshot_before": str(screenshot_before),
            "screenshot_order_window": str(order_window),
            "screenshot_symbol_selected": str(symbol_screenshot),
            "screenshot_market_mode": str(market_mode_screenshot),
            "screenshot_fields_filled": str(fields_screenshot),
            "submit_orders": self.config.submit_orders,
        }

        if not self.config.submit_orders:
            self.logger.warning(
                "Market submit is disabled. Prepared market position only: %s",
                prepared,
            )
            return prepared

        submit_screenshot = self.submit_market_position(str(position.get("type", "")))
        prepared["screenshot_submitted"] = str(submit_screenshot)
        return prepared

    def submit_pending_order(self) -> Path:
        self._require_armed("submit pending order")
        coordinates = self.config.order_form_coordinates
        if "place" not in coordinates:
            raise GuiSafetyError("Missing coordinates for order field: place")

        x, y = coordinates["place"]
        self.pyautogui.click(x, y)
        time.sleep(self.config.order_window_delay_seconds)
        screenshot_path = self.screenshot("after_place_clicked")
        self.logger.info("Clicked pending order Place button: %s", screenshot_path)
        return screenshot_path

    def submit_market_position(self, trade_type: str) -> Path:
        self._require_armed("submit market position")
        coordinates = self.config.order_form_coordinates
        button_name = "market_buy" if trade_type == "BUY" else "market_sell"
        if button_name not in coordinates:
            raise GuiSafetyError(f"Missing coordinates for order field: {button_name}")

        x, y = coordinates[button_name]
        self.pyautogui.click(x, y)
        time.sleep(self.config.order_window_delay_seconds)
        screenshot_path = self.screenshot("after_market_clicked")
        self.logger.info("Clicked market %s button: %s", trade_type, screenshot_path)
        self.accept_active_dialog()
        return screenshot_path

    def modify_pending_order_sl_tp(self, destination_ticket: str, sl: Any, tp: Any) -> Path:
        self._require_armed("modify pending order sl/tp")
        self.open_existing_order_dialog(destination_ticket)
        coordinates = self.config.order_form_coordinates
        self._replace_field_text("modify_sl", _format_number(sl), coordinates)
        self._replace_field_text("modify_tp", _format_number(tp), coordinates)

        if "modify" not in coordinates:
            raise GuiSafetyError("Missing coordinates for order field: modify")

        x, y = coordinates["modify"]
        self.pyautogui.click(x, y)
        time.sleep(self.config.order_window_delay_seconds)
        screenshot_path = self.screenshot("after_modify_sl_tp_clicked")
        self.logger.info(
            "Modified pending order destination_ticket=%s sl=%s tp=%s screenshot=%s",
            destination_ticket,
            sl,
            tp,
            screenshot_path,
        )
        self.accept_active_dialog()
        return screenshot_path

    def delete_pending_order(
        self,
        destination_ticket: str,
        row_center: tuple[int, int] | None = None,
    ) -> Path:
        self._require_armed("delete pending order")
        self.open_existing_order_dialog(destination_ticket, row_center=row_center)
        coordinates = self.config.order_form_coordinates

        if "delete" not in coordinates:
            raise GuiSafetyError("Missing coordinates for order field: delete")

        x, y = coordinates["delete"]
        self.pyautogui.click(x, y)
        time.sleep(self.config.order_window_delay_seconds)
        screenshot_path = self.screenshot("after_delete_pending_clicked")
        self.logger.info(
            "Deleted pending order destination_ticket=%s screenshot=%s",
            destination_ticket,
            screenshot_path,
        )
        self.accept_active_dialog()
        return screenshot_path

    def modify_position_sl_tp(
        self,
        destination_ticket: str,
        sl: Any,
        tp: Any,
        row_center: tuple[int, int] | None = None,
    ) -> Path:
        self._require_armed("modify position sl/tp")
        self.open_existing_position_modify_dialog(destination_ticket, row_center=row_center)
        coordinates = self.config.order_form_coordinates
        self._replace_field_text("position_modify_sl", _format_number(sl), coordinates)
        self._replace_field_text("position_modify_tp", _format_number(tp), coordinates)

        if "position_modify" not in coordinates:
            raise GuiSafetyError("Missing coordinates for position field: position_modify")

        x, y = coordinates["position_modify"]
        self.pyautogui.click(x, y)
        time.sleep(self.config.order_window_delay_seconds)
        screenshot_path = self.screenshot("after_position_modify_sl_tp_clicked")
        if self._trade_dialog_is_open():
            self.logger.warning(
                "Position modify dialog remained open after click destination_ticket=%s; dismissing dialog.",
                destination_ticket,
            )
            self._dismiss_active_dialog()
        self.logger.info(
            "Modified position destination_ticket=%s sl=%s tp=%s screenshot=%s",
            destination_ticket,
            sl,
            tp,
            screenshot_path,
        )
        self.accept_active_dialog()
        return screenshot_path

    def close_position(
        self,
        destination_ticket: str,
        row_center: tuple[int, int] | None = None,
        trade_type: str | None = None,
    ) -> Path:
        self._require_armed("close position")
        if row_center is None:
            screenshot_path = self.screenshot("position_close_ticket_not_found")
            raise GuiSafetyError(
                f"Destination position ticket {destination_ticket} has no verified row center. Screenshot: {screenshot_path}"
            )
        return self.close_position_from_context_menu(
            destination_ticket,
            row_center=row_center,
            trade_type=trade_type,
        )

    def close_position_from_context_menu(
        self,
        destination_ticket: str,
        row_center: tuple[int, int] | None = None,
        trade_type: str | None = None,
    ) -> Path:
        self._require_armed("close position from context menu")
        self.close_active_dialog()
        self.focus_mt5()
        if row_center is not None:
            self.pyautogui.rightClick(*self._clamp_point_to_screen(row_center))
        elif not self._right_click_ticket_row(destination_ticket):
            screenshot_path = self.screenshot("position_context_close_ticket_not_found")
            raise GuiSafetyError(
                f"Destination position ticket {destination_ticket} was not visible for context close. "
                f"Screenshot: {screenshot_path}"
            )

        time.sleep(self.config.field_delay_seconds)
        coordinates = self.config.order_form_coordinates
        if "position_context_close" in coordinates:
            self.pyautogui.click(*coordinates["position_context_close"])
        else:
            self.pyautogui.press("down", presses=1)
            self.pyautogui.press("enter")
        time.sleep(self.config.order_window_delay_seconds)
        if self._trade_dialog_is_open():
            self._submit_position_close_dialog(trade_type)
        screenshot_path = self.screenshot("after_position_context_close_clicked")
        self.logger.info(
            "Closed position from context menu destination_ticket=%s screenshot=%s",
            destination_ticket,
            screenshot_path,
        )
        self.accept_active_dialog()
        return screenshot_path

    def _submit_position_close_dialog(self, trade_type: str | None) -> None:
        coordinates = self.config.order_form_coordinates
        trade_type_upper = str(trade_type or "").strip().upper()
        if trade_type_upper == "BUY":
            button_key = "market_sell"
        elif trade_type_upper == "SELL":
            button_key = "market_buy"
        else:
            button_key = ""

        self.pyautogui.press("enter")
        time.sleep(self.config.order_window_delay_seconds)
        if not self._trade_dialog_is_open():
            return

        if button_key and button_key in coordinates:
            self.pyautogui.click(*coordinates[button_key])
        else:
            self.pyautogui.press("enter")
        time.sleep(self.config.order_window_delay_seconds)

    def _accept_one_click_terms_if_open(self) -> bool:
        if not self._dialog_title_is_open("Trading con un clic"):
            return False

        coordinates = self.config.order_form_coordinates
        checkbox = coordinates.get("one_click_terms_checkbox", (738, 654))
        accept = coordinates.get("one_click_accept", (1068, 654))
        self.pyautogui.click(*self._clamp_point_to_screen(checkbox))
        time.sleep(self.config.field_delay_seconds)
        self.pyautogui.click(*self._clamp_point_to_screen(accept))
        time.sleep(self.config.order_window_delay_seconds)
        self.logger.info("Accepted MT5 one-click trading terms for position close.")
        return True

    def open_existing_order_dialog(
        self,
        destination_ticket: str,
        row_center: tuple[int, int] | None = None,
    ) -> Path:
        self._require_armed("open existing order dialog")
        candidates = self._order_row_candidates(row_center)
        if not candidates:
            candidates = [None]

        for page in range(self.config.order_search_scroll_pages + 1):
            for candidate in candidates:
                self._dismiss_active_dialog()
                self.focus_mt5()
                if not self._click_order_row_by_ticket(destination_ticket, row_center=candidate):
                    continue

                time.sleep(self.config.order_window_delay_seconds)
                if not self._order_dialog_is_open():
                    continue

                if self._trade_dialog_title_contains(str(destination_ticket)):
                    screenshot_path = self.screenshot("modify_order_opened")
                    self.logger.info("Opened modify dialog for ticket=%s: %s", destination_ticket, screenshot_path)
                    return screenshot_path

                self.logger.warning(
                    "Opened a different order dialog while searching for ticket=%s page=%s candidate=%s",
                    destination_ticket,
                    page,
                    candidate,
                )

            if page < self.config.order_search_scroll_pages:
                self._dismiss_active_dialog()
                self._scroll_order_list_down(page + 1)

        screenshot_path = self.screenshot("modify_order_ticket_not_found")
        self._dismiss_active_dialog()
        raise GuiSafetyError(
            f"Could not open modify dialog for expected ticket {destination_ticket}. "
            f"Screenshot: {screenshot_path}"
        )

    def open_existing_position_modify_dialog(
        self,
        destination_ticket: str,
        row_center: tuple[int, int] | None = None,
    ) -> Path:
        self._require_armed("open existing position modify dialog")
        self.close_active_dialog()
        self.focus_mt5()
        if row_center is not None:
            self.pyautogui.rightClick(*self._clamp_point_to_screen(row_center))
        elif not self._right_click_ticket_row(destination_ticket):
            screenshot_path = self.screenshot("position_ticket_not_visible")
            raise GuiSafetyError(
                f"Destination position ticket {destination_ticket} was not visible in toolbox. "
                f"Screenshot: {screenshot_path}"
        )

        time.sleep(self.config.field_delay_seconds)
        coordinates = self.config.order_form_coordinates
        if "position_context_modify" not in coordinates:
            raise GuiSafetyError("Missing coordinates for position field: position_context_modify")
        self.pyautogui.click(*coordinates["position_context_modify"])
        time.sleep(self.config.order_window_delay_seconds)
        if not self._trade_dialog_is_open():
            screenshot_path = self.screenshot("modify_position_not_open")
            raise GuiSafetyError(
                f"Modify dialog did not open for destination position {destination_ticket}. "
                f"Screenshot: {screenshot_path}"
            )

        screenshot_path = self.screenshot("modify_position_opened")
        self.logger.info(
            "Opened modify position dialog for ticket=%s: %s",
            destination_ticket,
            screenshot_path,
        )
        return screenshot_path

    def accept_active_dialog(self) -> None:
        self._require_armed("accept active dialog")
        coordinates = self.config.order_form_coordinates
        if "accept" in coordinates:
            self.pyautogui.click(*coordinates["accept"])
        else:
            self.pyautogui.press("enter")
        time.sleep(self.config.field_delay_seconds)

    def _dismiss_active_dialog(self) -> None:
        self.pyautogui.press("esc")
        time.sleep(self.config.field_delay_seconds)

    def _click_order_row_by_ticket(
        self,
        destination_ticket: str,
        row_center: tuple[int, int] | None = None,
    ) -> bool:
        center = row_center or self._locate_ticket_center(destination_ticket)
        if center is None:
            return False
        self.pyautogui.doubleClick(*center)
        return True

    def _order_row_candidates(
        self,
        preferred: tuple[int, int] | None,
    ) -> list[tuple[int, int] | None]:
        candidates: list[tuple[int, int] | None] = []
        if preferred is not None:
            candidates.append(preferred)

        coordinates = self.config.order_form_coordinates
        anchor = coordinates.get("order_row_anchor")
        step = coordinates.get("order_row_step_y", (0, 20))
        scan_rows = int(coordinates.get("order_scan_rows", (0, 12))[1])
        if anchor is not None:
            anchor_x, anchor_y = anchor
            _, step_y = step
            max_y = coordinates.get("order_row_max_y", (0, 941))[1]
            for index in range(scan_rows):
                candidate = (anchor_x, anchor_y + (index * step_y))
                if candidate[1] > max_y:
                    continue
                if not self._point_inside_screen(candidate):
                    continue
                if candidate not in candidates:
                    candidates.append(candidate)

        if preferred is None:
            candidates.append(None)
        return candidates

    def _scroll_order_list_down(self, page: int) -> None:
        self.focus_mt5()
        coordinates = self.config.order_form_coordinates
        point = coordinates.get("order_list_scroll_point")
        if point is None:
            anchor = coordinates.get("order_row_anchor", (253, 741))
            scan_rows = int(coordinates.get("order_scan_rows", (0, 12))[1])
            _, step_y = coordinates.get("order_row_step_y", (0, 20))
            point = (anchor[0], anchor[1] + max(0, scan_rows - 1) * step_y)

        focus_point = self._last_visible_order_row_point()
        point = self._clamp_point_to_screen(focus_point or point)
        self.pyautogui.click(*point)
        time.sleep(self.config.field_delay_seconds)
        self.pyautogui.press("down", presses=max(1, self.config.order_search_arrow_down_presses))
        time.sleep(self.config.order_window_delay_seconds)
        self.logger.info(
            "Advanced destination order list while searching page=%s focus_point=%s arrow_down_presses=%s",
            page,
            point,
            self.config.order_search_arrow_down_presses,
        )

    def _last_visible_order_row_point(self) -> tuple[int, int] | None:
        candidates = [candidate for candidate in self._order_row_candidates(None) if candidate is not None]
        if not candidates:
            return None
        return max(candidates, key=lambda point: point[1])

    def _point_inside_screen(self, point: tuple[int, int]) -> bool:
        width, height = self.pyautogui.size()
        x, y = point
        return 0 <= x < width and 0 <= y < height

    def _clamp_point_to_screen(self, point: tuple[int, int]) -> tuple[int, int]:
        width, height = self.pyautogui.size()
        x, y = point
        return max(0, min(x, width - 1)), max(0, min(y, height - 1))

    def _right_click_ticket_row(self, destination_ticket: str) -> bool:
        center = self._locate_ticket_center(destination_ticket)
        if center is None:
            fallback = self.config.order_form_coordinates.get("position_row_fallback")
            if fallback is None:
                return False
            center = fallback
        self.pyautogui.rightClick(*center)
        return True

    def _locate_ticket_center(self, destination_ticket: str) -> tuple[int, int] | None:
        ticket = str(destination_ticket)
        centers = self._locate_ticket_centers([ticket])
        if ticket in centers:
            return centers[ticket]
        return self._known_visible_order_rows().get(ticket)

    def _locate_ticket_centers(self, tickets: list[str]) -> dict[str, tuple[int, int]]:
        wanted = {str(ticket).strip() for ticket in tickets if str(ticket).strip()}
        if not wanted:
            return {}
        screenshot = self.pyautogui.screenshot()
        try:
            import pytesseract  # type: ignore
        except ImportError:
            return {}

        centers: dict[str, tuple[int, int]] = {}
        text = pytesseract.image_to_data(screenshot, output_type=pytesseract.Output.DICT)
        for index, value in enumerate(text.get("text", [])):
            ticket = str(value).strip()
            if ticket in wanted:
                x = int(text["left"][index] + text["width"][index] / 2)
                y = int(text["top"][index] + text["height"][index] / 2)
                centers[ticket] = (x, y)
        return centers

    def _calibrate_row_group(
        self,
        prefix: str,
        rows: list[dict[str, Any]],
        ticket_centers: dict[str, tuple[int, int]],
    ) -> dict[str, tuple[int, int]]:
        indexed_points: list[tuple[int, tuple[int, int]]] = []
        for index, row in enumerate(rows):
            ticket = str(row.get("ticket", "")).strip()
            if ticket in ticket_centers:
                indexed_points.append((index, ticket_centers[ticket]))
        if not indexed_points:
            return {}

        coordinates = self.config.order_form_coordinates
        anchor_key = f"{prefix}_row_anchor"
        step_key = f"{prefix}_row_step_y"
        max_key = f"{prefix}_row_max_y"
        default_anchor = coordinates.get(
            anchor_key,
            coordinates.get("order_row_anchor", (253, 741)),
        )
        _, default_step_y = coordinates.get(step_key, coordinates.get("order_row_step_y", (0, 20)))
        step_y = _derive_step_y(indexed_points, default_step_y)
        first_index, first_point = min(indexed_points, key=lambda item: item[0])
        anchor_y = first_point[1] - (first_index * step_y)
        anchor_x = int(median([point[0] for _, point in indexed_points]) if indexed_points else default_anchor[0])
        _, screen_height = self._screen_size_tuple()
        visible_bottom = max(point[1] for _, point in indexed_points)
        max_y = min(screen_height - 1, max(visible_bottom + step_y, anchor_y))
        return {
            anchor_key: (anchor_x, int(anchor_y)),
            step_key: (0, int(step_y)),
            max_key: (0, int(max_y)),
        }

    def _fallback_toolbox_coordinates(self) -> dict[str, tuple[int, int]]:
        coordinates = self.config.order_form_coordinates
        updates: dict[str, tuple[int, int]] = {}
        for prefix in ("position", "order"):
            anchor_key = f"{prefix}_row_anchor"
            step_key = f"{prefix}_row_step_y"
            max_key = f"{prefix}_row_max_y"
            fallback_anchor = (
                coordinates.get(anchor_key)
                or coordinates.get("order_row_anchor")
                or coordinates.get("position_row_fallback")
                or (253, 741)
            )
            fallback_step = coordinates.get(step_key) or coordinates.get("order_row_step_y") or (0, 20)
            step_y = max(1, int(fallback_step[1]))
            scan_rows = int(coordinates.get(f"{prefix}_scan_rows", coordinates.get("order_scan_rows", (0, 12)))[1])
            _, screen_height = self._screen_size_tuple()
            configured_max = coordinates.get(max_key) or coordinates.get("order_row_max_y")
            if configured_max is not None:
                max_y = min(screen_height - 1, max(0, int(configured_max[1])))
            else:
                max_y = max(0, screen_height - 1 - 120)
            anchor_y = max(0, max_y - (max(1, scan_rows) - 1) * step_y)
            updates[anchor_key] = (int(fallback_anchor[0]), int(anchor_y))
            updates[step_key] = (0, int(step_y))
            updates[max_key] = (0, int(max_y))
        return updates

    def _screen_size_tuple(self) -> tuple[int, int]:
        size = self.pyautogui.size()
        if hasattr(size, "width") and hasattr(size, "height"):
            return int(size.width), int(size.height)
        return int(size[0]), int(size[1])

    def _click_visible_ticket_by_known_rows(self, destination_ticket: str) -> bool:
        visible_rows = self._known_visible_order_rows()
        if destination_ticket not in visible_rows:
            return False
        self.pyautogui.doubleClick(*visible_rows[destination_ticket])
        return True

    def _known_visible_order_rows(self) -> dict[str, tuple[int, int]]:
        # Current MT5 toolbox row positions with the terminal maximized at 1920x1080.
        return {
            "665895199": (253, 941),
            "665895249": (253, 921),
            "665895407": (253, 901),
            "665895470": (253, 881),
            "665895588": (253, 861),
            "665895701": (253, 841),
            "665895797": (253, 821),
            "665895854": (253, 801),
            "665896001": (253, 781),
            "665896072": (253, 761),
            "665896125": (253, 741),
        }

    def close_active_dialog(self) -> None:
        self._require_armed("close active dialog")
        self.focus_mt5()
        if "accept" in self.config.order_form_coordinates:
            self.pyautogui.click(*self.config.order_form_coordinates["accept"])
            time.sleep(self.config.field_delay_seconds)
        self.pyautogui.press("esc")
        time.sleep(self.config.field_delay_seconds)
        self.logger.info("Pressed Escape to clear any active MT5 dialog.")

    def select_symbol(self, symbol: str) -> Path:
        self._require_armed("select symbol")
        self._replace_field_text("symbol", symbol, self.config.order_form_coordinates)
        self.pyautogui.press("enter")
        time.sleep(self.config.order_window_delay_seconds)
        screenshot_path = self.screenshot("symbol_selected")
        self.logger.info("Selected symbol: %s", symbol)
        return screenshot_path

    def switch_to_pending_order_mode(self) -> Path:
        self._require_armed("switch to pending order mode")
        coordinates = self.config.order_form_coordinates
        if "execution_type" not in coordinates:
            raise GuiSafetyError("Missing coordinates for order field: execution_type")

        x, y = coordinates["execution_type"]
        self.pyautogui.click(x, y)
        time.sleep(self.config.field_delay_seconds)
        self.pyautogui.press("down")
        time.sleep(self.config.field_delay_seconds)
        self.pyautogui.press("enter")
        time.sleep(self.config.order_window_delay_seconds)
        screenshot_path = self.screenshot("pending_order_mode")
        self.logger.info("Switched order window to pending mode: %s", screenshot_path)
        return screenshot_path

    def switch_to_market_order_mode(self) -> Path:
        self._require_armed("switch to market order mode")
        if not self._order_dialog_is_open():
            screenshot_path = self.screenshot("market_order_not_open")
            raise GuiSafetyError(f"Market order dialog is not open. Screenshot: {screenshot_path}")

        time.sleep(self.config.order_window_delay_seconds)
        screenshot_path = self.screenshot("market_order_mode")
        self.logger.info("Using current order window market mode: %s", screenshot_path)
        return screenshot_path

    def fill_market_position_fields(self, position: dict[str, Any]) -> Path:
        self._require_armed("fill market position fields")
        coordinates = self.config.order_form_coordinates
        values = {
            "market_volume": _format_number(position.get("volume", "")),
            "market_sl": _format_number(position.get("sl", "")),
            "market_tp": _format_number(position.get("tp", "")),
        }

        for field_name, value in values.items():
            self._replace_field_text(field_name, value, coordinates)

        screenshot_path = self.screenshot("market_position_fields_filled")
        self.logger.info(
            "Filled market position fields: type=%s values=%s",
            position.get("type", ""),
            values,
        )
        return screenshot_path

    def fill_basic_order_fields(self, order: dict[str, Any]) -> Path:
        self._require_armed("fill basic order fields")
        coordinates = self.config.order_form_coordinates
        pending_type_screenshot = self._select_pending_type(str(order.get("type", "")), coordinates)
        self.logger.info("Pending type selection screenshot: %s", pending_type_screenshot)

        values = {
            "volume": _format_number(order.get("volume_current", order.get("volume_initial", ""))),
            "price_open": _format_number(order.get("price_open", "")),
            "sl": _format_number(order.get("sl", "")),
            "tp": _format_number(order.get("tp", "")),
        }

        for field_name, value in values.items():
            self._replace_field_text(field_name, value, coordinates)

        screenshot_path = self.screenshot("order_basic_fields_filled")
        self.logger.info(
            "Filled basic pending order fields: type=%s values=%s",
            order.get("type", ""),
            values,
        )
        return screenshot_path

    def _select_pending_type(
        self,
        pending_type: str,
        coordinates: dict[str, tuple[int, int]],
    ) -> Path:
        if "pending_type" not in coordinates:
            raise GuiSafetyError("Missing coordinates for order field: pending_type")

        option_steps = {
            "BUY_LIMIT": 0,
            "SELL_LIMIT": 1,
            "BUY_STOP": 2,
            "SELL_STOP": 3,
            "BUY_STOP_LIMIT": 4,
            "SELL_STOP_LIMIT": 5,
        }
        if pending_type not in option_steps:
            raise GuiSafetyError(f"Unsupported pending order type: {pending_type}")

        x, y = coordinates["pending_type"]
        self.pyautogui.click(x, y)
        time.sleep(self.config.field_delay_seconds)
        self.pyautogui.hotkey("alt", "down")
        time.sleep(self.config.field_delay_seconds)
        base_x, base_y = coordinates.get("pending_type_option_base", (x, y + 17))
        _, step_y = coordinates.get("pending_type_option_step_y", (0, 14))
        option_y = base_y + (option_steps[pending_type] * step_y)
        self.pyautogui.click(base_x, option_y)
        time.sleep(self.config.field_delay_seconds)
        self.logger.info("Selected pending order type: %s", pending_type)
        return self.screenshot("pending_type_selected")

    def _replace_field_text(
        self,
        field_name: str,
        value: str,
        coordinates: dict[str, tuple[int, int]],
    ) -> None:
        if field_name not in coordinates:
            raise GuiSafetyError(f"Missing coordinates for order field: {field_name}")
        x, y = coordinates[field_name]
        self.pyautogui.click(x, y)
        time.sleep(self.config.field_delay_seconds)
        self.pyautogui.hotkey("ctrl", "a")
        time.sleep(self.config.field_delay_seconds)
        self.paste_text(value)
        time.sleep(self.config.field_delay_seconds)

    def _matching_windows(self):
        title = self.config.window_title_contains
        try:
            return self.pyautogui.getWindowsWithTitle(title)
        except Exception:
            self.logger.exception("Window lookup failed.")
            return []

    def _order_dialog_is_open(self) -> bool:
        return self._dialog_title_is_open(self.config.order_dialog_title_contains)

    def _trade_dialog_is_open(self) -> bool:
        return self._order_dialog_is_open() or self._dialog_title_is_open("Posición")

    def _trade_dialog_title_contains(self, text: str) -> bool:
        expected = str(text)
        for title_contains in (self.config.order_dialog_title_contains, "Posición"):
            try:
                windows = self.pyautogui.getWindowsWithTitle(title_contains)
            except Exception:
                continue
            for window in windows:
                if expected in getattr(window, "title", ""):
                    return True
        return False

    def _dialog_title_is_open(self, title_contains: str) -> bool:
        try:
            windows = self.pyautogui.getWindowsWithTitle(title_contains)
        except Exception:
            self.logger.exception("Dialog lookup failed for title: %s", title_contains)
            return False
        return any(
            title_contains.lower() in getattr(window, "title", "").lower()
            for window in windows
        )

    def _require_armed(self, action_name: str) -> None:
        if not self.config.armed_for_trading:
            raise GuiSafetyError(
                f"Blocked GUI action '{action_name}'. Set executor.armed_for_trading=true "
                "only after validating the workflow on a demo terminal."
            )

    @staticmethod
    def _load_pyautogui():
        try:
            import pyautogui
        except ImportError as exc:
            raise GuiDependencyError(
                "PyAutoGUI is not installed. Run: py -m pip install -r requirements.txt"
            ) from exc
        return pyautogui


def _format_number(value: Any) -> str:
    if value in {None, ""}:
        return ""
    if isinstance(value, float):
        text = f"{value:.10f}".rstrip("0").rstrip(".")
        return text if text else "0"
    return str(value)


def _derive_step_y(
    indexed_points: list[tuple[int, tuple[int, int]]],
    default_step_y: int,
) -> int:
    deltas: list[int] = []
    ordered = sorted(indexed_points, key=lambda item: item[0])
    for left, right in zip(ordered, ordered[1:]):
        left_index, left_point = left
        right_index, right_point = right
        index_delta = right_index - left_index
        if index_delta <= 0:
            continue
        y_delta = right_point[1] - left_point[1]
        if y_delta <= 0:
            continue
        deltas.append(round(y_delta / index_delta))
    if not deltas:
        return int(default_step_y)
    return max(1, int(median(deltas)))
