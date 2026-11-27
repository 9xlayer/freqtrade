"""Strict topological layer-isolation and import-matrix rules (LINT003-030)."""

from __future__ import annotations

import ast
from pathlib import Path

from scripts.guardrails.manifest import DEFAULT_MANIFEST, ArchitectureManifest


_FORBIDDEN_UPPER_LAYERS = (
    "signals",
    "directional",
    "execution",
    "risk",
    "intelligence",
    "telemetry",
    "strategy_mtf_sniper",
)


def resolve_source_layer(rel_posix: str) -> str | None:
    if rel_posix == "__init__.py":
        return "__init__"
    if rel_posix == "strategy_mtf_sniper.py":
        return "facade"
    top = rel_posix.split("/", 1)[0]
    if top in DEFAULT_MANIFEST.layer_allowed_imports:
        return top
    return None


def resolve_imported_modules(node: ast.AST, rel_posix: str) -> list[str]:
    """Extract normalized module targets for both absolute and relative imports."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names if alias.name]
    if not isinstance(node, ast.ImportFrom):
        return []

    if node.level == 0:
        return [node.module] if node.module else []

    parts = [p for p in rel_posix.split("/")[:-1] if p]
    up = node.level - 1
    base_parts = parts[: max(0, len(parts) - up)] if up <= len(parts) else []
    suffix = node.module or ""
    joined = ".".join([p for p in (*base_parts, suffix) if p])
    if joined:
        return [f"strategies.mtf_sniper.{joined}"]
    return [f"strategies.mtf_sniper.{alias.name}" for alias in node.names if alias.name]


def extract_internal_target_layer(module_name: str, pkg_name: str = "mtf_sniper") -> str | None:
    marker = f"{pkg_name}."
    if module_name == pkg_name or module_name.endswith(f".{pkg_name}"):
        return "__root__"
    if marker in module_name:
        rest = module_name.split(marker, 1)[1]
        head = rest.split(".", 1)[0]
        return "facade" if head == "strategy_mtf_sniper" else head
    return None


def _matches_subpkg(module_name: str, subpkg: str) -> bool:
    return f"mtf_sniper.{subpkg}" in module_name or f".{subpkg}" in module_name


def _check_directional_and_sensor_codes(
    module_name: str,
    rel_posix: str,
    lineno: int,
    py_path: Path,
    violations: list[str],
) -> None:
    if "directional/long" in rel_posix and "directional.short" in module_name:
        violations.append(
            f"{py_path}:{lineno} LINT003: LongPolicy cross-imports Short ({module_name}). "
            "Directional trading paths must remain strictly isolated."
        )
    if "directional/short" in rel_posix and "directional.long" in module_name:
        violations.append(
            f"{py_path}:{lineno} LINT003: ShortPolicy cross-imports Long ({module_name}). "
            "Directional trading paths must remain strictly isolated."
        )
    if "signals/sensors" in rel_posix and (
        "execution" in module_name or "strategy_mtf_sniper" in module_name
    ):
        violations.append(
            f"{py_path}:{lineno} LINT004: "
            "Sensor module cannot import upward from execution pipeline or strategy."
        )


def _check_upward_layer_codes(
    module_name: str,
    rel_posix: str,
    lineno: int,
    py_path: Path,
    violations: list[str],
) -> None:
    layer_bans = (
        (
            "signals/",
            (
                "directional",
                "execution",
                "risk",
                "intelligence",
                "telemetry",
                "strategy_mtf_sniper",
            ),
            "LINT020",
            "Signals layer cannot import upward from",
        ),
        (
            "directional/",
            ("execution", "risk", "intelligence", "telemetry", "strategy_mtf_sniper"),
            "LINT023",
            "Directional layer cannot import",
        ),
        (
            "core/",
            ("market", *_FORBIDDEN_UPPER_LAYERS),
            "LINT007",
            "Core domain cannot import higher layer",
        ),
        (
            "market/",
            _FORBIDDEN_UPPER_LAYERS,
            "LINT008",
            "Market module cannot import higher layer",
        ),
    )
    for prefix, banned_set, code, label in layer_bans:
        if not rel_posix.startswith(prefix):
            continue
        for forbidden in banned_set:
            if _matches_subpkg(module_name, forbidden):
                violations.append(
                    f"{py_path}:{lineno} {code}: {label} '{forbidden}' ({module_name})."
                )


def _check_facade_codes(
    module_name: str,
    lineno: int,
    py_path: Path,
    violations: list[str],
) -> None:
    if py_path.name != "strategy_mtf_sniper.py" and "strategy_mtf_sniper" in module_name:
        violations.append(
            f"{py_path}:{lineno} LINT009: "
            f"Internal subsystem cannot import Strategy facade ({module_name}). "
            "Prevents circular dependencies back to the Freqtrade entrypoint."
        )
    if py_path.name == "strategy_mtf_sniper.py" and (
        "talib" in module_name or "technical" in module_name
    ):
        violations.append(
            f"{py_path}:{lineno} LINT017: "
            f"Strategy facade cannot import indicator libraries directly ({module_name}). "
            "Delegate technical computing to market/indicators.py or sensors."
        )


def check_import_node(
    node: ast.AST,
    rel_posix: str,
    py_path: Path,
    violations: list[str],
    manifest: ArchitectureManifest = DEFAULT_MANIFEST,
) -> None:
    lineno = getattr(node, "lineno", 0)
    src_layer = resolve_source_layer(rel_posix)
    allowed_targets = (
        manifest.layer_allowed_imports.get(src_layer) if src_layer is not None else None
    )

    for module_name in resolve_imported_modules(node, rel_posix):
        _check_directional_and_sensor_codes(module_name, rel_posix, lineno, py_path, violations)
        _check_upward_layer_codes(module_name, rel_posix, lineno, py_path, violations)
        _check_facade_codes(module_name, lineno, py_path, violations)
        if allowed_targets is None:
            continue
        target_layer = extract_internal_target_layer(module_name, manifest.package_name)
        if target_layer is not None and target_layer not in allowed_targets:
            allowed_display = sorted(allowed_targets)
            violations.append(
                f"{py_path}:{lineno} LINT030: Layer '{src_layer}' cannot import internal layer "
                f"'{target_layer}' ({module_name}). Allowed internal targets: {allowed_display}."
            )
