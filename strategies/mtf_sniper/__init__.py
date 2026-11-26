"""MTF Sniper + JEV — single source of version truth.

Bump here only; strategy and Decision engine import these constants.
Docs (`ARCHITECTURE.md`) should match ``STRATEGY_VERSION``.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Package / live strategy (layered pipeline)
# ---------------------------------------------------------------------------
STRATEGY_VERSION = "4.7.0-1w-regime"
__version__ = STRATEGY_VERSION

# ---------------------------------------------------------------------------
# Decision Gate identity (confirm path + SQLite cache keys)
# Bump when native rules / prompt / payload / cache schema identity changes.
# ---------------------------------------------------------------------------
DECISION_ENGINE_VERSION = "2.7.2"
DECISION_MODEL_VERSION_NATIVE = f"native-system1-v{DECISION_ENGINE_VERSION}"
DECISION_PROMPT_VERSION_NATIVE = f"decision-native-v{DECISION_ENGINE_VERSION}"
DECISION_PROMPT_VERSION_EXTERNAL = (
    f"decision-external-prompt-v{DECISION_ENGINE_VERSION}-sniper-mtf-richctx"
)
# Override via JEV_DECISION_MODEL_VERSION (default alias jev-latest).
DECISION_MODEL_VERSION_EXTERNAL_DEFAULT = "jev-latest"

__all__ = [
    "__version__",
    "STRATEGY_VERSION",
    "DECISION_ENGINE_VERSION",
    "DECISION_MODEL_VERSION_NATIVE",
    "DECISION_PROMPT_VERSION_NATIVE",
    "DECISION_PROMPT_VERSION_EXTERNAL",
    "DECISION_MODEL_VERSION_EXTERNAL_DEFAULT",
]
