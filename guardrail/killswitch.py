"""Sector 3 — kill switch (physical, obvious, file-based).

A single, trivially inspectable on/off: ``<repo>/logs/killswitch.json`` renders
``armed: true`` the moment someone fires it. The *gate* consults this every time
it would grant approval — and it is checked BEFORE any hard rule, so re-arming
mid-flight stops even a trade whose every rule would otherwise pass.

Not a decoration: Sector 4 (execution) is expected to observe the same file and
refuse to broadcast while it is armed. Here, at the guardrail layer, armed means
a hard ``killed`` verdict for every proposal from that instant onward.

Mechanism documented for the demo:
  - file ``logs/killswitch.json`` with ``{armed, reason, armed_at, source}``
  - ``arm()`` writes it; ``disarm()`` clears it; ``is_armed()`` reads it
  - default path derived from this repo's ``logs/`` dir — the test injects a
    temp path so a real kill never leaks into the repo state.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


def _default_path() -> Path:  # noqa: ANN202
    here = Path(__file__).resolve().parent          # .../guardrail
    return here.parent / "logs" / "killswitch.json"  # .../logs/killswitch.json


class KillSwitch:  # noqa: D101
    def __init__(self, path: Optional[str | Path] = None) -> None:  # noqa: D107
        self.path = Path(path) if path is not None else _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def arm(self, reason: str = "manual", source: str = "sector3") -> dict:  # noqa: ANN201, D102
        from datetime import datetime, timezone
        state = {
            "armed": True,
            "reason": reason or "manual",
            "armed_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
        }
        self.path.write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8")
        return state

    def disarm(self) -> dict:  # noqa: ANN201, D102
        state = {"armed": False, "reason": "", "armed_at": None, "source": "sector3"}
        self.path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        return state

    def is_armed(self) -> bool:  # noqa: ANN201, D102
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            return bool(state.get("armed"))
        except (FileNotFoundError, json.JSONDecodeError, PermissionError,
                OSError):
            return False

    def state(self) -> dict:  # noqa: ANN201, D102
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, PermissionError,
                OSError):
            return {"armed": False, "reason": "", "armed_at": None,
                    "source": "sector3", "error": "kill file unreadable"}

    def __str__(self) -> str:  # noqa: D105, ANN201
        return f"KillSwitch(armed={self.is_armed()}, {self.path})"


# module-level singleton convenience (already armed on disk? no — disarm default)
_default = None


def get_default() -> KillSwitch:  # noqa: ANN201
    global _default  # noqa: PLW0603
    if _default is None:
        _default = KillSwitch()
    return _default