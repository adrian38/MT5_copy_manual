from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import ChangeEvent


def append_events(path: Path, events: list[ChangeEvent]) -> None:
    if not events:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for event in events:
            payload = event.to_dict()
            payload["detected_at_utc"] = datetime.now(timezone.utc).isoformat()
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
