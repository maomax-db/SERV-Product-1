"""Sector 3 — daily-approved-trade counter (persistent, file-backed).

Mechanism documented (hackathon-simple but durable): the count of approvals for
the current UTC day is stored append-only in ``logs/daily_count_{YYYY-MM-DD}.jsonl``
— one JSON line per approval, each carrying ``{utc_date, approved_at, asset,
size}``. ``DailyCount.pending()`` reads the file for today's date and returns
how many approvals are already on the book; the gate uses that number against
``policy.max_daily_trades``. Persisting as a file means a restart cannot reset
the counter and a human can read it as plain text; path is injectable so the
test writes into a temp dir and stays deterministic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _default_dir() -> Path:  # noqa: ANN202
    here = Path(__file__).resolve().parent
    return here.parent / "logs"


def _today_utc() -> str:  # noqa: ANN202
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class DailyCount:  # noqa: D101
    def __init__(self, log_dir: Path | str | None = None) -> None:  # noqa: D107, ANN204
        self.log_dir = Path(log_dir) if log_dir else _default_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _file(self, utc_date: str | None = None) -> Path:  # noqa: ANN201
        d = utc_date or _today_utc()
        return self.log_dir / f"daily_count_{d}.jsonl"

    def pending(self, utc_date: str | None = None) -> int:  # noqa: ANN201
        """Approvals already recorded for the UTC day (0 if none)."""
        p = self._file(utc_date)
        try:
            return len(p.read_text(encoding="utf-8").splitlines())
        except (FileNotFoundError, OSError):
            return 0

    def record(self, *, asset: str, size: float,  # noqa: ANN201
               utc_date: str | None = None) -> None:
        p = self._file(utc_date)
        obj = {
            "utc_date": utc_date or _today_utc(),
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "asset": asset,
            "size": size,
        }
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj) + "\n")

    def read_all(self, utc_date: str | None = None) -> list:  # noqa: ANN201
        try:
            return [json.loads(l) for l in
                    self._file(utc_date).read_text(encoding="utf-8").splitlines()]
        except (FileNotFoundError, OSError):
            return []