"""Declarative Architecture-as-Code manifest locking MTF Sniper topology and budgets."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArchitectureManifest:
    """Single source of truth for allowed layers, DAG edges, file hygiene, and size caps."""

    package_name: str = "mtf_sniper"

    allowed_subdirs: frozenset[str] = frozenset(
        {
            "core",
            "market",
            "signals",
            "signals/sensors",
            "directional",
            "directional/long",
            "directional/short",
            "execution",
            "intelligence",
            "risk",
            "telemetry",
            "docs",
        }
    )

    allowed_root_files: frozenset[str] = frozenset(
        {
            "__init__.py",
            "strategy_mtf_sniper.py",
            "strategy_mtf_sniper.json",
        }
    )

    # Strict topological DAG allowlist: source_layer -> allowed internal target layers.
    # "__root__" represents package root metadata (`strategies.mtf_sniper` -> STRATEGY_VERSION).
    layer_allowed_imports: dict[str, frozenset[str]] = field(
        default_factory=lambda: {
            "__init__": frozenset(),
            "core": frozenset({"__root__", "core"}),
            "market": frozenset({"market"}),
            "signals": frozenset({"core", "market", "signals"}),
            "directional": frozenset({"core", "market", "signals", "directional"}),
            "telemetry": frozenset({"core", "telemetry"}),
            "risk": frozenset({"core", "risk"}),
            "intelligence": frozenset({"__root__", "core", "directional", "intelligence"}),
            "execution": frozenset({"core", "market", "signals", "directional", "execution"}),
            "facade": frozenset(
                {
                    "__root__",
                    "core",
                    "market",
                    "execution",
                    "intelligence",
                    "risk",
                    "telemetry",
                }
            ),
        }
    )

    slop_filename_regex: re.Pattern[str] = field(
        default_factory=lambda: re.compile(
            r"(?:^|_)(?:v\d+|new|old|bak|backup|copy|temp|tmp|draft|scratch|misc|util|utils|helper|helpers)(?:_|\.py$)",
            re.IGNORECASE,
        )
    )

    max_subsystem_file_lines: int = 550
    file_line_overrides: dict[str, int] = field(
        default_factory=lambda: {
            "strategy_mtf_sniper.py": 1250,
            "intelligence/jev_decision.py": 1500,
        }
    )
    max_function_lines: int = 165
    function_line_exempt_files: frozenset[str] = frozenset({"intelligence/jev_decision.py"})

    authoritative_docs: tuple[str, ...] = (
        "docs/README.md",
        "docs/ARCHITECTURE.md",
        "docs/DATA_CONTRACTS.md",
        "docs/PHILOSOPHY.md",
        "docs/GUARDRAILS.md",
    )
    required_guardrail_codes: tuple[str, ...] = tuple(f"LINT{i:03d}" for i in range(1, 37))
    banned_legacy_doc_symbols: tuple[str, ...] = (
        "HunterEscalationGateway",
        "JevEntryHunter",
        "jev_hunter_waterfall_collapse",
        "jev_hunter_breakout_expansion",
        "jev_hunter_cache",
    )


DEFAULT_MANIFEST = ArchitectureManifest()
