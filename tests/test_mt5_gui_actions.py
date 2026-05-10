import unittest
from pathlib import Path

from src.mt5_copy.mt5_gui import GuiConfig, GuiSafetyError, Mt5GuiController


class FakePyAutoGui:
    def __init__(self):
        self.calls = []

    def click(self, *args):
        self.calls.append(("click", args))

    def rightClick(self, *args):
        self.calls.append(("rightClick", args))

    def doubleClick(self, *args):
        self.calls.append(("doubleClick", args))

    def hotkey(self, *args):
        self.calls.append(("hotkey", args))

    def press(self, key, presses=1):
        self.calls.append(("press", (key, presses)))

    def size(self):
        return (1366, 768)


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


class RecordingMt5GuiController(Mt5GuiController):
    def __init__(self, config):
        self.config = config
        self.logger = FakeLogger()
        self.pyautogui = FakePyAutoGui()
        self.pasted = []
        self.screenshots = []
        self.opened_orders = []
        self.opened_positions = []

    def focus_mt5(self):
        return True

    def screenshot(self, name="mt5"):
        self.screenshots.append(name)
        return Path(f"{name}.png")

    def paste_text(self, text):
        self.pasted.append(text)
        self.pyautogui.hotkey("ctrl", "v")

    def _order_dialog_is_open(self):
        return True

    def _trade_dialog_is_open(self):
        return False

    def _dialog_title_is_open(self, title_contains):
        return False

    def open_existing_order_dialog(self, destination_ticket, row_center=None):
        self.opened_orders.append((destination_ticket, row_center))
        return Path("modify_order_opened.png")

    def open_existing_position_modify_dialog(self, destination_ticket, row_center=None):
        self.opened_positions.append((destination_ticket, row_center))
        return Path("modify_position_opened.png")


class Mt5GuiActionsTest(unittest.TestCase):
    def test_prepare_pending_order_clicks_fields_type_and_place(self):
        gui = RecordingMt5GuiController(_config())

        prepared = gui.prepare_pending_order(
            {
                "ticket": "1",
                "symbol": "XAUUSD",
                "type": "SELL_STOP",
                "volume_current": "0.01",
                "price_open": "100.0",
                "sl": "90.0",
                "tp": "120.0",
            }
        )

        self.assertEqual(prepared["screenshot_submitted"], "after_place_clicked.png")
        self.assertIn(("press", ("f9", 1)), gui.pyautogui.calls)
        self.assertIn(("click", _coords()["pending_type"]), gui.pyautogui.calls)
        self.assertIn(("click", (746, 359)), gui.pyautogui.calls)
        self.assertIn(("click", _coords()["place"]), gui.pyautogui.calls)
        self.assertEqual(gui.pasted[:5], ["XAUUSD", "0.01", "100.0", "90.0", "120.0"])

    def test_prepare_market_position_clicks_buy_and_sell_paths(self):
        for trade_type, button in (("BUY", "market_buy"), ("SELL", "market_sell")):
            with self.subTest(trade_type=trade_type):
                gui = RecordingMt5GuiController(_config())

                prepared = gui.prepare_market_position(
                    {
                        "ticket": "42",
                        "symbol": "XAUUSD",
                        "type": trade_type,
                        "volume": "0.01",
                        "sl": "90.0",
                        "tp": "120.0",
                    }
                )

                self.assertEqual(prepared["screenshot_submitted"], "after_market_clicked.png")
                self.assertIn(("click", _coords()["execution_type"]), gui.pyautogui.calls)
                self.assertIn(("hotkey", ("home",)), gui.pyautogui.calls)
                self.assertIn(("click", _coords()[button]), gui.pyautogui.calls)
                self.assertEqual(gui.pasted[:4], ["XAUUSD", "0.01", "90.0", "120.0"])

    def test_modify_pending_order_sl_tp_clicks_modify_button(self):
        gui = RecordingMt5GuiController(_config())

        screenshot = gui.modify_pending_order_sl_tp("77", "91.0", "121.0")

        self.assertEqual(screenshot, Path("after_modify_sl_tp_clicked.png"))
        self.assertEqual(gui.opened_orders, [("77", None)])
        self.assertIn(("click", _coords()["modify"]), gui.pyautogui.calls)
        self.assertEqual(gui.pasted, ["91.0", "121.0"])

    def test_modify_position_sl_tp_uses_row_center_and_position_modify_button(self):
        gui = RecordingMt5GuiController(_config())

        screenshot = gui.modify_position_sl_tp("99", "91.0", "121.0", row_center=(180, 513))

        self.assertEqual(screenshot, Path("after_position_modify_sl_tp_clicked.png"))
        self.assertEqual(gui.opened_positions, [("99", (180, 513))])
        self.assertIn(("click", _coords()["position_modify"]), gui.pyautogui.calls)
        self.assertEqual(gui.pasted, ["91.0", "121.0"])

    def test_close_position_uses_context_menu_hotkeys_instead_of_x_button(self):
        gui = RecordingMt5GuiController(_config())

        screenshot = gui.close_position("99", row_center=(180, 513), trade_type="BUY")

        self.assertEqual(screenshot, Path("after_position_context_close_clicked.png"))
        self.assertIn(("rightClick", (180, 513)), gui.pyautogui.calls)
        self.assertIn(("click", _coords()["position_context_close"]), gui.pyautogui.calls)
        self.assertNotIn(("click", (1358, 513)), gui.pyautogui.calls)

    def test_close_position_context_menu_selects_close_command(self):
        gui = RecordingMt5GuiController(_config())

        screenshot = gui.close_position_from_context_menu("99", row_center=(180, 513), trade_type="BUY")

        self.assertEqual(screenshot, Path("after_position_context_close_clicked.png"))
        self.assertIn(("rightClick", (180, 513)), gui.pyautogui.calls)
        self.assertIn(("click", _coords()["position_context_close"]), gui.pyautogui.calls)

    def test_calibrates_toolbox_row_coordinates_from_visible_tickets(self):
        class CalibratingGui(RecordingMt5GuiController):
            def _locate_ticket_centers(self, tickets):
                return {
                    "10": (254, 600),
                    "11": (254, 620),
                    "20": (254, 660),
                    "21": (254, 680),
                }

        gui = CalibratingGui(_config())

        updates = gui.calibrate_toolbox_coordinates(
            destination_positions=[{"ticket": "10"}, {"ticket": "11"}],
            destination_orders=[{"ticket": "20"}, {"ticket": "21"}],
        )

        self.assertEqual(updates["position_row_anchor"], (254, 600))
        self.assertEqual(updates["position_row_step_y"], (0, 20))
        self.assertEqual(updates["position_row_max_y"], (0, 640))
        self.assertEqual(updates["order_row_anchor"], (254, 660))
        self.assertEqual(updates["order_row_step_y"], (0, 20))
        self.assertEqual(updates["order_row_max_y"], (0, 700))
        self.assertEqual(gui.config.order_form_coordinates["position_row_anchor"], (254, 600))

    def test_calibrates_toolbox_even_without_visible_tickets(self):
        gui = RecordingMt5GuiController(_config())

        updates = gui.calibrate_toolbox_coordinates(
            destination_positions=[],
            destination_orders=[],
        )

        self.assertIn("position_row_anchor", updates)
        self.assertIn("order_row_anchor", updates)
        self.assertEqual(updates["position_row_step_y"], (0, 20))
        self.assertEqual(updates["order_row_step_y"], (0, 20))
        self.assertEqual(updates["position_row_max_y"], (0, 647))
        self.assertEqual(updates["order_row_max_y"], (0, 647))

    def test_calibration_requires_focused_mt5(self):
        class UnfocusedGui(RecordingMt5GuiController):
            def focus_mt5(self):
                return False

        gui = UnfocusedGui(_config())

        with self.assertRaises(GuiSafetyError):
            gui.calibrate_toolbox_coordinates([], [])


def _config():
    return GuiConfig(
        window_title_contains="67192119",
        screenshot_dir=Path("screenshots"),
        image_dir=Path("images"),
        image_confidence=0.85,
        action_pause_seconds=0,
        fail_safe=True,
        armed_for_trading=True,
        submit_orders=True,
        new_order_hotkey=("f9",),
        new_order_button=(348, 42),
        order_dialog_title_contains="Orden",
        order_window_delay_seconds=0,
        field_delay_seconds=0,
        comment_prefix="COPY_",
        order_form_coordinates=_coords(),
    )


def _coords():
    return {
        "symbol": (825, 263),
        "execution_type": (825, 284),
        "pending_type": (746, 316),
        "pending_type_option_base": (746, 329),
        "pending_type_option_step_y": (0, 10),
        "volume": (746, 336),
        "price_open": (746, 356),
        "sl": (746, 376),
        "tp": (902, 376),
        "modify": (716, 445),
        "modify_sl": (746, 336),
        "modify_tp": (902, 336),
        "position_modify_sl": (746, 316),
        "position_modify_tp": (746, 336),
        "position_modify": (794, 409),
        "position_context_modify": (270, 353),
        "position_context_close": (270, 329),
        "position_close_x": (1358, 513),
        "market_volume": (746, 316),
        "market_sl": (746, 336),
        "market_tp": (902, 336),
        "market_sell": (716, 451),
        "market_buy": (872, 451),
        "accept": (717, 474),
        "place": (794, 475),
    }


if __name__ == "__main__":
    unittest.main()
