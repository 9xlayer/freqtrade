"""Append-only decision ledger (ACCEPT / REJECT with reason codes)."""
from __future__ import annotations

import json
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List, Optional

from strategies.mtf_sniper.core.contracts import TelemetryDecisionRecord


class DecisionLedger:
    """
    Persists TelemetryDecisionRecord rows as JSONL.

    Thread-safe append. Also keeps an in-memory buffer for unit tests / inspection.
    """

    def __init__(self, path: Optional[str | Path] = None, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._buffer: List[TelemetryDecisionRecord] = []
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def __getstate__(self) -> dict:
        # threading.Lock is not picklable; hyperopt loky workers need this.
        state = self.__dict__.copy()
        state.pop("_lock", None)
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._lock = threading.Lock()

    def record(self, row: TelemetryDecisionRecord) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._buffer.append(row)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(row), ensure_ascii=True) + "\n")

    def record_dict(self, **kwargs) -> TelemetryDecisionRecord:
        row = TelemetryDecisionRecord(**kwargs)
        self.record(row)
        return row

    def all(self) -> List[TelemetryDecisionRecord]:
        with self._lock:
            return list(self._buffer)

    def rejected(self) -> List[TelemetryDecisionRecord]:
        return [r for r in self.all() if r.final_action == "REJECT"]

    def entered(self) -> List[TelemetryDecisionRecord]:
        return [r for r in self.all() if r.final_action == "ENTER"]

    def clear_buffer(self) -> None:
        with self._lock:
            self._buffer.clear()

    def load_from_disk(self) -> Iterable[TelemetryDecisionRecord]:
        if self.path is None or not self.path.exists():
            return []
        rows: List[TelemetryDecisionRecord] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                rows.append(TelemetryDecisionRecord(**payload))
        return rows
