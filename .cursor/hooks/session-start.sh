#!/usr/bin/env bash
# sessionStart: inject Memory Bank + GitNexus ritual context
set -euo pipefail
cat >/dev/null || true

status_line="GitNexus status unavailable"
if command -v gitnexus >/dev/null 2>&1; then
  status_line="$(gitnexus status 2>/dev/null | tr '\n' ' ' | sed 's/  */ /g' | cut -c1-280 || true)"
elif [[ -x ".gitnexus/run.cjs" ]]; then
  status_line="$(node .gitnexus/run.cjs status 2>/dev/null | tr '\n' ' ' | sed 's/  */ /g' | cut -c1-280 || true)"
fi

active="missing"
progress="missing"
[[ -f memory-bank/activeContext.md ]] && active="present"
[[ -f memory-bank/progress.md ]] && progress="present"

python3 - "$active" "$progress" "$status_line" <<'PY'
import json, sys
active, progress, status = sys.argv[1], sys.argv[2], sys.argv[3]
ctx = f"""[freqtrade agent bootstrap]
1) Read memory-bank/activeContext.md and memory-bank/progress.md first (files: active={active}, progress={progress}).
2) Prefer GitNexus graph tools over blind repo-wide grep. Repo index name: freqtrade.
3) GitNexus status snapshot: {status}
4) If index is stale/missing, run: gitnexus analyze
5) Before editing symbols: impact analysis. Before commit: detect_changes. After commit: gitnexus analyze. Update Memory Bank only at milestone boundaries.
6) Before finishing strategy edits: run `.venv/bin/python scripts/lint_guardrails.py --verify-all` (AST/DAG/Anti-Slop LINT001-036 + Pyrefly + Ruff + strategy pytest)."""
print(json.dumps({"additional_context": ctx}))
PY
exit 0
