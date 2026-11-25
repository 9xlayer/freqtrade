"""Shared bounded JSONL append helper with size-based log rotation."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any


DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024


def append_jsonl_row(path: Path, row: Any, *, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> None:
    """Append a dataclass row as JSONL, rotating ``path`` to ``<path>.1`` when over limit."""
    if max_file_bytes > 0 and path.exists() and path.stat().st_size >= max_file_bytes:
        backup = path.with_name(f"{path.name}.1")
        path.replace(backup)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(row), ensure_ascii=True) + "\n")
