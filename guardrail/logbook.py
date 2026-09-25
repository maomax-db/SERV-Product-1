"""Sector 3 — append-only audit log (the Sector 5 dashboard's raw material).

Every decision the engine makes — blocked-by-rule, held-for-low-confidence,
approved, rejected-by-human, killed — lands here as ONE JSON line with the
fields Sector 5 needs. Path is injectable (test writes to a temp dir) but the
default is the live ``logs/`` folder so a real run is inspectable immediately.

Row shape (documented, stable):

    {event, verdict, decision{...}, blocked_by, error_type, confidence,
     timestamp_utc, latency_ms, rationale, model, network, chain_id,
     block_number}

Rationale is carried through VERBATIM from Sector 2 — this layer never
rewrites a reason SERV actually gave; it only records what it got.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _default_dir() -> Path:  # noqa: ANN202
    here = Path(__file__).resolve().parent
    return here.parent / "logs"


def _ts() -> str:  # noqa: ANN202
    return datetime.now(timezone.utc).isoformat()


class AuditLog:  # noqa: D101
    def __init__(self, log_dir: Path | str | None = None) -> None:  # noqa: D107, ANN204
        self.log_dir = Path(log_dir) if log_dir else _default_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.log_dir / "sector3_audit.jsonl"

    def append(self, row: dict) -> Path:  # noqa: ANN201, ANN202
        row.setdefault("timestamp_utc", _ts())
        with self._file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return self._file

    def read_all(self) -> list:  # noqa: ANN201
        try:
            return [json.loads(l) for l in
                    self._file.read_text(encoding="utf-8").splitlines()]
        except (FileNotFoundError, OSError):
            return []