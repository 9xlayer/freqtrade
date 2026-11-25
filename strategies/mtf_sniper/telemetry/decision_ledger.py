"""Append-only decision ledger (ACCEPT / REJECT with reason codes)."""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Iterable
from pathlib import Path

from strategies.mtf_sniper.core.contracts import TelemetryDecisionRecord
from strategies.mtf_sniper.telemetry.jsonl_io import DEFAULT_MAX_FILE_BYTES, append_jsonl_row


DEFAULT_MAX_BUFFER = 2000


class DecisionLedger:
    """
    Persists TelemetryDecisionRecord rows as JSONL.

    Thread-safe append with bounded in-memory buffer and size-based JSONL rotation.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        enabled: bool = True,
        max_buffer: int | None = DEFAULT_MAX_BUFFER,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        self.enabled = enabled
        self.path = Path(path) if path else None
        self.max_buffer = max_buffer
        self.max_file_bytes = max_file_bytes
        self._lock = threading.Lock()
        self._buffer: deque[TelemetryDecisionRecord] = deque(
            maxlen=max_buffer if (max_buffer is not None and max_buffer > 0) else None
        )
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
                append_jsonl_row(self.path, row, max_file_bytes=self.max_file_bytes)

    def record_dict(self, **kwargs) -> TelemetryDecisionRecord:
        row = TelemetryDecisionRecord(**kwargs)
        self.record(row)
        return row

    def all(self) -> list[TelemetryDecisionRecord]:
        with self._lock:
            return list(self._buffer)

    def rejected(self) -> list[TelemetryDecisionRecord]:
        return [r for r in self.all() if r.final_action == "REJECT"]

    def entered(self) -> list[TelemetryDecisionRecord]:
        return [r for r in self.all() if r.final_action == "ENTER"]

    def clear_buffer(self) -> None:
        with self._lock:
            self._buffer.clear()

    def load_from_disk(self) -> Iterable[TelemetryDecisionRecord]:
        if self.path is None or not self.path.exists():
            return []
        rows: list[TelemetryDecisionRecord] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                rows.append(TelemetryDecisionRecord(**payload))
        return rows
