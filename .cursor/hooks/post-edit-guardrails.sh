#!/usr/bin/env bash
# postToolUse: run fast MTF Sniper architectural & anti-slop guardrails after edits
set -euo pipefail

input="$(cat || true)"
printf '%s' "$input" | python3 -c '
import json
import subprocess
import sys
from pathlib import Path

raw = sys.stdin.read() or "{}"
try:
    payload = json.loads(raw)
except Exception:
    print("{}")
    raise SystemExit(0)

blob = json.dumps(payload)
watched_tokens = ("strategies/mtf_sniper", "scripts/guardrails", "scripts/lint_guardrails.py")
if not any(tok in blob for tok in watched_tokens):
    print("{}")
    raise SystemExit(0)

py = ".venv/bin/python" if Path(".venv/bin/python").is_file() else sys.executable
proc = subprocess.run(
    [py, "scripts/lint_guardrails.py", "--fast"],
    capture_output=True,
    text=True,
    check=False,
)
if proc.returncode == 0:
    print("{}")
    raise SystemExit(0)

details = (proc.stdout or proc.stderr or "").strip()
msg = (
    "[MTF SNIPER ARCHITECTURE GUARDRAIL VIOLATION — FIX IMMEDIATELY]\n"
    f"{details}\n"
    "After fixing, run `.venv/bin/python scripts/lint_guardrails.py --verify-all`."
)
print(json.dumps({"additional_context": msg}))
'
exit 0
