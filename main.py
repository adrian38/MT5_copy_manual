"""Entry point for PyInstaller bundle and direct execution."""
from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from mt5_copy.config import DEFAULT_CONFIG_PATH  # noqa: E402
from mt5_copy.ui import run_ui  # noqa: E402

run_ui(DEFAULT_CONFIG_PATH)
