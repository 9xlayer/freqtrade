"""Dynamic sub-system registry, recipe priority, policy protocol, and sensor coverage checks."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def validate_recipe_and_specs(
    recipes: tuple[Any, ...],
    specs: tuple[Any, ...],
    violations: list[str],
) -> None:
    recipe_map = {r.name: r for r in recipes}
    if len(recipe_map) != len(recipes):
        violations.append("LINT024: Duplicate SetupRecipe.name in all_live_recipes().")

    priorities_by_side: dict[str, dict[int, str]] = {}
    for r in recipes:
        if r.side not in ("long", "short") or not r.required_sensors or not r.subsystem:
            violations.append(f"LINT024: Recipe '{r.name}' has invalid side/sensors/subsystem.")
            continue
        side_prios = priorities_by_side.setdefault(r.side, {})
        prev_name = side_prios.get(r.priority)
        if prev_name is not None and prev_name != r.name:
            violations.append(
                f"LINT024: Ambiguous same-side SetupRecipe.priority={r.priority} on side "
                f"'{r.side}' shared by '{prev_name}' and '{r.name}'. Assign distinct priorities."
            )
        side_prios[r.priority] = r.name

    for spec in specs:
        rec = recipe_map.get(spec.recipe_name)
        if rec is None or rec.side != spec.side or rec.subsystem != spec.subsystem_id:
            violations.append(
                f"LINT024: Subsystem '{spec.subsystem_id}' recipe mismatch: {spec.recipe_name!r}."
            )


def check_policy_modules_and_engine(
    specs: tuple[Any, ...],
    protocol: type,
    violations: list[str],
) -> None:
    seen_modules: dict[str, str] = {}
    for spec in specs:
        prev = seen_modules.get(spec.policy_module)
        if prev is not None:
            violations.append(
                f"LINT025: Subsystem '{spec.subsystem_id}' shares policy_module "
                f"'{spec.policy_module}' with '{prev}'. "
                "Each directional sub-system must have its own dedicated Policy class."
            )
        seen_modules[spec.policy_module] = spec.subsystem_id

        try:
            mod_name, cls_name = spec.policy_module.rsplit(".", 1)
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, cls_name)
            instance = cls()
            if not isinstance(instance, protocol):
                violations.append(
                    f"LINT025: Policy '{spec.policy_module}' for '{spec.subsystem_id}' "
                    "does not satisfy DirectionalPolicyProtocol."
                )
        except Exception as exc:
            violations.append(
                f"LINT025: Failed to load policy '{spec.policy_module}' for "
                f"'{spec.subsystem_id}': {exc}"
            )


def check_engine_wiring(
    specs: tuple[Any, ...],
    protocol: type,
    violations: list[str],
) -> None:
    from strategies.mtf_sniper.directional.engine import DirectionalTradingEngine

    engine = DirectionalTradingEngine()
    policies = getattr(engine, "subsystem_policies", None)
    if not isinstance(policies, dict):
        violations.append("LINT026: DirectionalTradingEngine.subsystem_policies dict is missing.")
        return
    for spec in specs:
        pol = policies.get(spec.subsystem_id)
        if pol is None or not isinstance(pol, protocol):
            violations.append(
                f"LINT026: DirectionalTradingEngine.subsystem_policies missing valid "
                f"protocol implementation for '{spec.subsystem_id}'."
            )


def check_sensor_coverage(
    recipes: tuple[Any, ...],
    known_sensors: set[str],
    violations: list[str],
) -> None:
    for rec in recipes:
        missing = sorted(set(rec.required_sensors) - known_sensors)
        if missing:
            violations.append(
                f"LINT028: Recipe '{rec.name}' references missing sensor(s) "
                f"{missing} not implemented in signals/sensors/."
            )


def check_subsystem_registry(root_dir: Path, violations: list[str]) -> None:
    """LINT024-028: Validate sub-systems, dedicated policies, priority order, and sensors."""
    try:
        pkg_root = str(root_dir.resolve().parent.parent)
        if pkg_root not in sys.path:
            sys.path.insert(0, pkg_root)
        from strategies.mtf_sniper.directional.setup_recipes import (
            SUBSYSTEM_REGISTRY,
            DirectionalPolicyProtocol,
            all_live_recipes,
        )
        from strategies.mtf_sniper.signals.sensor_suite import SensorSuite
    except Exception as exc:
        violations.append(f"LINT024: Failed to load subsystem registry: {exc}")
        return

    recipes = all_live_recipes(include_waterfall=True)
    validate_recipe_and_specs(recipes, SUBSYSTEM_REGISTRY, violations)
    check_policy_modules_and_engine(SUBSYSTEM_REGISTRY, DirectionalPolicyProtocol, violations)
    check_engine_wiring(SUBSYSTEM_REGISTRY, DirectionalPolicyProtocol, violations)

    dummy_strat = SimpleNamespace(custom_pair_params={})
    known_sensors = {
        *(s.name for s in SensorSuite.live(dummy_strat).sensors),
        *(s.name for s in SensorSuite.full().sensors),
    }
    check_sensor_coverage(recipes, known_sensors, violations)
