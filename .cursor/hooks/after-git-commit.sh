#!/usr/bin/env bash
# afterShellExecution: remind Memory Bank + GitNexus refresh after successful git commit
set -euo pipefail
input="$(cat || true)"
printf '%s' "$input" | python3 -c '
import json, sys, re
raw = sys.stdin.read() or "{}"
try:
    data = json.loads(raw)
except Exception:
    data = {}
cmd = str(data.get("command") or "")
exit_code = data.get("exitCode", data.get("exit_code", 0))
try:
    exit_code = int(exit_code)
except Exception:
    exit_code = 0
if exit_code != 0 or not re.search(r"(^|[\s;|&])git\s+commit(\s|$)", cmd):
    print("{}")
    raise SystemExit(0)
msg = (
    "Commit succeeded. Next: (1) run `gitnexus analyze` to refresh the freqtrade index; "
    "(2) if this was a milestone boundary, update memory-bank/activeContext.md and "
    "memory-bank/progress.md; (3) optional pre-commit `detect_changes` should already have run."
)
print(json.dumps({"additional_context": msg}))
'
exit 0
