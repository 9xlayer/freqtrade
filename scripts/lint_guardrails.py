#!/usr/bin/env python3
"""
MTF Sniper Architectural & Anti-AI-Slop Guardrail CLI (`LINT001`-`LINT036`).

Usage:
  .venv/bin/python scripts/lint_guardrails.py strategies/mtf_sniper
  .venv/bin/python scripts/lint_guardrails.py --fast
  .venv/bin/python scripts/lint_guardrails.py --subsystems
  .venv/bin/python scripts/lint_guardrails.py --verify-all
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.guardrails.anti_slop_rules import (  # noqa: E402
    check_ast_anti_slop,
    check_docs_contract_sync,
    check_lexical_slop_line,
    check_package_topology,
)
from scripts.guardrails.layer_rules import check_import_node  # noqa: E402
from scripts.guardrails.manifest import DEFAULT_MANIFEST, ArchitectureManifest  # noqa: E402
from scripts.guardrails.registry_rules import (  # noqa: E402
    check_policy_modules_and_engine,
    check_sensor_coverage,
    check_subsystem_registry,
)


class ArchitectureGuardrailLinter:
    """Multi-layer static + runtime guardrail verifier for strategies/mtf_sniper."""

    def __init__(
        self,
        root_dir: str = "strategies/mtf_sniper",
        *,
        strict_tools: bool = False,
        skip_external_tools: bool = False,
        manifest: ArchitectureManifest = DEFAULT_MANIFEST,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.strict_tools = strict_tools
        self.skip_external_tools = skip_external_tools
        self.manifest = manifest
        self.violations: list[str] = []
        self._line_counts: dict[Path, int] = {}
        self.vn_pattern = re.compile(
            r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]",
            re.IGNORECASE,
        )

    def run(self) -> int:
        self.violations.clear()
        self._line_counts.clear()
        check_package_topology(
            self.root_dir,
            is_mtf_package=self._is_mtf_package(),
            violations=self.violations,
            manifest=self.manifest,
        )
        self._check_comment_and_lexical_policy()
        self._check_ast_rules()
        self._check_subsystem_registry()
        if self._is_mtf_package():
            check_docs_contract_sync(self.root_dir, self.violations, self.manifest)
        if not self.skip_external_tools:
            self._check_pyrefly()
            self._check_ruff()

        if not self.violations:
            print("OK: All architectural guardrails and lint rules passed cleanly (0 violations).")
            return 0

        print(f"FAILED: Found {len(self.violations)} architectural guardrail violation(s):\n")
        for v in self.violations:
            print(f"  [X] {v}")
        print(
            "\nRemediation: Resolve root cause, obey ArchitectureManifest DAG and anti-slop rules."
        )
        return 1

    def _check_comment_and_lexical_policy(self) -> None:
        prohibited_words = [
            (
                re.compile(
                    r"#\s*(workaround|hack|fixme|quickfix|bypass_rule|temporary\s+bypass)",
                    re.IGNORECASE,
                ),
                (
                    "LINT001: Prohibited workaround/hack comment detected. "
                    "Resolve root cause instead of using justifications."
                ),
            )
        ]

        for py_path in sorted(self.root_dir.rglob("*.py")):
            total_lines = 0
            comment_lines = 0
            with py_path.open(encoding="utf-8", errors="ignore") as f:
                for line_no, line in enumerate(f, 1):
                    total_lines += 1
                    s = line.strip()

                    if py_path.name != "lint_guardrails.py" and self.vn_pattern.search(line):
                        self.violations.append(
                            f"{py_path}:{line_no} LINT012: "
                            "Non-English text detected in code/comment. "
                            "Enforce Rule 2: 100% English in codebase."
                        )

                    check_lexical_slop_line(line, s, line_no, py_path, self.violations)

                    if s.startswith("#"):
                        comment_lines += 1
                        for pattern, msg in prohibited_words:
                            if pattern.search(s):
                                self.violations.append(f"{py_path}:{line_no} {msg}")

            self._line_counts[py_path] = total_lines
            if total_lines > 50:
                density = comment_lines / total_lines
                if density > 0.05:
                    self.violations.append(
                        f"{py_path}: LINT002: Comment density ({density * 100:.1f}%) "
                        "exceeds 5.0% threshold. "
                        "Code must be self-documenting (99% No-Comment Policy)."
                    )

    @staticmethod
    def _get_type_name(t_node: ast.AST) -> str | None:
        if isinstance(t_node, ast.Name):
            return t_node.id
        if (
            isinstance(t_node, ast.Subscript)
            and isinstance(t_node.value, ast.Name)
            and t_node.value.id == "Optional"
            and isinstance(t_node.slice, ast.Name)
        ):
            return t_node.slice.id
        return None

    def _collect_class_fields(self, tree: ast.AST) -> dict[str, dict[str, str]]:
        class_fields: dict[str, dict[str, str]] = {}
        for node in tree.body if isinstance(tree, ast.Module) else []:
            if isinstance(node, ast.ClassDef):
                c_types = {}
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        t = self._get_type_name(item.annotation)
                        if t:
                            c_types[item.target.id] = t
                if c_types:
                    class_fields[node.name] = c_types
        return class_fields

    def _check_class_node(self, node: ast.ClassDef, rel_posix: str, py_path: Path) -> None:
        if "core/contracts.py" in rel_posix:
            is_dc = False
            is_frozen = False
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name) and dec.id == "dataclass":
                    is_dc = True
                elif (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Name)
                    and dec.func.id == "dataclass"
                ):
                    is_dc = True
                    for k in dec.keywords:
                        if (
                            k.arg == "frozen"
                            and isinstance(k.value, ast.Constant)
                            and k.value.value is True
                        ):
                            is_frozen = True
            if is_dc and not is_frozen:
                self.violations.append(
                    f"{py_path}:{node.lineno} LINT016: "
                    f"Contract dataclass '{node.name}' must have frozen=True "
                    "to enforce immutability across layer boundaries."
                )

        if (
            "signals/sensors" in rel_posix
            and node.name.endswith("Sensor")
            and node.name != "BaseSensor"
        ):
            base_names = [b.id for b in node.bases if isinstance(b, ast.Name)]
            if "BaseSensor" not in base_names:
                self.violations.append(
                    f"{py_path}:{node.lineno} LINT018: "
                    f"Concrete sensor '{node.name}' must inherit from BaseSensor."
                )

    def _check_assign_node(self, node: ast.Assign, rel_posix: str, py_path: Path) -> None:
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == "environ"
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "JEV_MODE"
            ):
                self.violations.append(
                    f"{py_path}:{node.lineno} LINT005: "
                    "Direct mutation of os.environ['JEV_MODE'] is prohibited. "
                    "Use runtime profiles or explicit function arguments."
                )
            if (
                "execution/entry_pipeline.py" in rel_posix
                and isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == "loc"
            ):
                self.violations.append(
                    f"{py_path}:{node.lineno} LINT013: "
                    "Label assignment via .loc is prohibited in entry_pipeline. "
                    "Use NumPy boolean masks or positional .iloc[pos] indexing."
                )
            if (
                "directional/setup_composer.py" in rel_posix
                and isinstance(target, ast.Attribute)
                and target.attr == "_by_side"
            ):
                self.violations.append(
                    f"{py_path}:{node.lineno} LINT022: "
                    "Single-recipe-per-side mapping (_by_side) is prohibited. "
                    "Use multi-subsystem recipes_grouped_by_side."
                )

    def _check_engine_dispatch_ast(self, tree: ast.AST, py_path: Path) -> None:
        source_names = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        if "subsystem_policies" not in source_names:
            self.violations.append(
                f"{py_path}: LINT026: DirectionalTradingEngine must maintain and dispatch "
                "through `subsystem_policies` registry instead of side-only branching."
            )

    @staticmethod
    def _extract_str_constants(node: ast.AST) -> set[str]:
        return {
            sub.value
            for sub in ast.walk(node)
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
        }

    def _check_pipeline_subsystem_values(
        self, tree: ast.AST, py_path: Path, valid_subsystems: set[str]
    ) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for k, v in zip(node.keys, node.values, strict=False):
                if isinstance(k, ast.Constant) and k.value == "subsystem" and v is not None:
                    for candidate_val in self._extract_str_constants(v):
                        if candidate_val not in valid_subsystems:
                            self.violations.append(
                                f"{py_path}:{node.lineno} LINT027: Unknown subsystem tag "
                                f"{candidate_val!r} in EntryPipeline. Register in "
                                "SUBSYSTEM_REGISTRY first."
                            )

    def _check_pipeline_single_truth(self, tree: ast.AST, py_path: Path) -> None:
        banned_names = {
            "_candidates_from_sniper_masks",
            "_candidates_for_mask",
            "MomentumRadar",
            "cached_sniper_parts",
            "LegacySniperPartSensor",
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in banned_names
            ):
                self.violations.append(
                    f"{py_path}:{node.lineno} LINT029: Forbidden fast-path method "
                    f"'{node.name}' in EntryPipeline. Use SetupComposer.compose_suite()."
                )
            elif isinstance(node, ast.Name) and node.id in banned_names:
                self.violations.append(
                    f"{py_path}:{node.lineno} LINT029: Forbidden fast-path symbol "
                    f"'{node.id}' in execution layer. Route all signals via SetupComposer."
                )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "TradeCandidate"
            ):
                self.violations.append(
                    f"{py_path}:{node.lineno} LINT029: Direct TradeCandidate() construction "
                    "in execution layer bypasses SetupComposer."
                )

    def _check_pipeline_subsystem_tag_ast(self, tree: ast.AST, py_path: Path) -> None:
        source_names = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        if "compose_suite" not in source_names:
            self.violations.append(
                f"{py_path}: LINT026: EntryPipeline must dispatch signals through "
                "SetupComposer.compose_suite() (Single Truth)."
            )
            return
        self._check_pipeline_single_truth(tree, py_path)
        if self._is_mtf_package():
            try:
                from strategies.mtf_sniper.directional.setup_recipes import SUBSYSTEM_REGISTRY

                valid_ids = {s.subsystem_id for s in SUBSYSTEM_REGISTRY}
                self._check_pipeline_subsystem_values(tree, py_path, valid_ids)
            except Exception as exc:
                self.violations.append(f"{py_path}: LINT027: {exc}")

    def _check_misc_node(self, node: ast.AST, py_path: Path, rel_posix: str = "") -> None:
        if (
            "signals/sensors" in rel_posix
            and isinstance(node, ast.Constant)
            and node.value in ("enter_long", "enter_short", "exit_long", "exit_short")
        ):
            self.violations.append(
                f"{py_path}:{node.lineno} LINT021: "
                f"Sensor cannot emit/mutate trade column '{node.value}'. "
                "Return TriggerEvents only."
            )
        if (
            "directional/setup_composer.py" in rel_posix
            and isinstance(node, ast.DictComp)
            and isinstance(node.key, ast.Attribute)
            and node.key.attr == "side"
            and isinstance(node.value, ast.Name)
        ):
            self.violations.append(
                f"{py_path}:{node.lineno} LINT022: "
                "1-to-1 {r.side: r} dict comprehension overwrites multiple subsystems per side."
            )
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.match(r"^jev-hunter-[a-zA-Z0-9_-]+$", node.value)
        ):
            self.violations.append(
                f"{py_path}:{node.lineno} LINT006: Invalid model ID '{node.value}'. "
                "Only canonical alias 'jev-latest' is permitted under Rule 5."
            )

        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "shift"
            and node.args
            and isinstance(node.args[0], ast.UnaryOp)
            and isinstance(node.args[0].op, ast.USub)
        ):
            self.violations.append(
                f"{py_path}:{node.lineno} LINT010: "
                "Negative shift (shift(-...)) lookahead bias is strictly prohibited."
            )

        if isinstance(node, ast.ExceptHandler):
            is_bare = node.type is None
            is_broad = isinstance(node.type, ast.Name) and node.type.id in (
                "Exception",
                "BaseException",
            )
            if (is_bare or is_broad) and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                self.violations.append(
                    f"{py_path}:{node.lineno} LINT011: "
                    "Silent exception swallowing (except Exception: pass) is prohibited. "
                    "Catch specific typed exceptions or log warnings."
                )

    def _check_cast_subnode(
        self,
        subnode: ast.Call,
        fn_name: str,
        py_path: Path,
        var_types: dict[str, str],
        class_fields: dict[str, dict[str, str]],
    ) -> None:
        arg = subnode.args[0]
        lineno = subnode.lineno
        if isinstance(arg, ast.Constant):
            matches = (
                (fn_name == "str" and isinstance(arg.value, str))
                or (fn_name == "int" and isinstance(arg.value, int))
                or (fn_name == "float" and isinstance(arg.value, float))
                or (fn_name == "bool" and isinstance(arg.value, bool))
            )
            if matches:
                self.violations.append(
                    f"{py_path}:{lineno} LINT014: "
                    f"Redundant type cast {fn_name}({arg.value!r}) on literal."
                )
        elif fn_name == "bool" and isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.Not):
            self.violations.append(
                f"{py_path}:{lineno} LINT014: Redundant bool(...) call on `not` expression."
            )
        elif fn_name == "bool" and isinstance(arg, ast.Compare):
            self.violations.append(
                f"{py_path}:{lineno} LINT014: Redundant bool(...) call on comparison expression."
            )
        elif isinstance(arg, ast.Name) and arg.id in var_types and var_types[arg.id] == fn_name:
            self.violations.append(
                f"{py_path}:{lineno} LINT014: Redundant type cast {fn_name}({arg.id}); "
                f"parameter '{arg.id}' is already of type '{fn_name}'."
            )
        elif (
            isinstance(arg, ast.Attribute)
            and isinstance(arg.value, ast.Name)
            and arg.value.id in var_types
        ):
            cls_name = var_types[arg.value.id]
            if cls_name in class_fields and class_fields[cls_name].get(arg.attr) == fn_name:
                self.violations.append(
                    f"{py_path}:{lineno} LINT014: "
                    f"Redundant type cast {fn_name}({arg.value.id}.{arg.attr}); "
                    f"attribute '{arg.attr}' is already of type '{fn_name}' in {cls_name}."
                )

    def _check_function_redundant_casts(
        self,
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        py_path: Path,
        class_fields: dict[str, dict[str, str]],
    ) -> None:
        var_types: dict[str, str] = {}
        all_args = list(func_node.args.args) + list(func_node.args.kwonlyargs)
        if func_node.args.vararg:
            all_args.append(func_node.args.vararg)
        if func_node.args.kwarg:
            all_args.append(func_node.args.kwarg)
        for arg in all_args:
            if arg and arg.annotation:
                t = self._get_type_name(arg.annotation)
                if t:
                    var_types[arg.arg] = t

        for subnode in ast.walk(func_node):
            if not (isinstance(subnode, ast.Call) and isinstance(subnode.func, ast.Name)):
                continue
            fn_name = subnode.func.id
            if fn_name in ("str", "int", "float", "bool") and subnode.args:
                self._check_cast_subnode(subnode, fn_name, py_path, var_types, class_fields)

    def _check_ast_rules(self) -> None:
        class_fields: dict[str, dict[str, str]] = {}
        parsed_trees: dict[Path, ast.AST] = {}

        for py_path in sorted(self.root_dir.rglob("*.py")):
            try:
                source = py_path.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(py_path))
                parsed_trees[py_path] = tree
                class_fields.update(self._collect_class_fields(tree))
            except Exception as e:
                self.violations.append(f"{py_path}: LINT999: Failed to parse AST: {e}")

        for py_path, tree in parsed_trees.items():
            rel_posix = py_path.relative_to(self.root_dir).as_posix()
            total_lines = self._line_counts.get(py_path, 0)
            check_ast_anti_slop(
                tree,
                rel_posix,
                total_lines,
                py_path,
                self.violations,
                self.manifest,
            )

            if rel_posix == "directional/engine.py":
                self._check_engine_dispatch_ast(tree, py_path)
            elif rel_posix == "execution/entry_pipeline.py":
                self._check_pipeline_subsystem_tag_ast(tree, py_path)

            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    check_import_node(node, rel_posix, py_path, self.violations, self.manifest)
                elif isinstance(node, ast.ClassDef):
                    self._check_class_node(node, rel_posix, py_path)
                elif isinstance(node, ast.Assign):
                    self._check_assign_node(node, rel_posix, py_path)
                else:
                    self._check_misc_node(node, py_path, rel_posix)

            for func_node in [
                n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]:
                self._check_function_redundant_casts(func_node, py_path, class_fields)

    @staticmethod
    def _find_pyrefly_binary() -> str | None:
        which = shutil.which("pyrefly")
        if which:
            return which
        home = Path.home()
        candidates = [
            *home.glob(".antigravity-ide/extensions/meta.pyrefly-*/bin/pyrefly"),
            *home.glob(".cursor/extensions/meta.pyrefly-*/bin/pyrefly"),
            *home.glob(".vscode/extensions/meta.pyrefly-*/bin/pyrefly"),
            Path(".venv/bin/pyrefly"),
        ]
        for c in candidates:
            if c.is_file() and os.access(c, os.X_OK):
                return str(c)
        return None

    def _tool_targets(self) -> list[str]:
        targets = [str(self.root_dir)]
        if self._is_mtf_package():
            for extra in (Path("scripts/lint_guardrails.py"), Path("scripts/guardrails")):
                if extra.exists():
                    targets.append(str(extra))
        return targets

    def _check_pyrefly(self) -> None:
        binary = self._find_pyrefly_binary()
        if not binary:
            if self.strict_tools:
                self.violations.append(
                    "LINT015 (Pyrefly): Binary not found under --strict. "
                    "Install pyrefly into .venv or PATH."
                )
            return

        try:
            result = subprocess.run(
                [binary, "check", *self._tool_targets()],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in (result.stdout or "").splitlines():
                line_str = line.strip()
                if line_str.startswith(("ERROR", "WARN")):
                    self.violations.append(f"LINT015 (Pyrefly): {line_str}")
            if result.returncode != 0 and not any("LINT015" in v for v in self.violations):
                for line in (result.stderr or "").splitlines():
                    line_str = line.strip()
                    if (
                        line_str.startswith("ERROR")
                        and "PYTHONPATH" not in line_str
                        and "older than" not in line_str
                    ):
                        self.violations.append(f"LINT015 (Pyrefly): {line_str}")
        except Exception as exc:
            self.violations.append(f"LINT015 (Pyrefly): Execution error: {exc}")

    @staticmethod
    def _find_ruff_binary() -> str | None:
        which = shutil.which("ruff")
        if which:
            return which
        venv_ruff = Path(".venv/bin/ruff")
        if venv_ruff.is_file() and os.access(venv_ruff, os.X_OK):
            return str(venv_ruff)
        return None

    def _check_ruff(self) -> None:
        binary = self._find_ruff_binary()
        if not binary:
            if self.strict_tools:
                self.violations.append(
                    "LINT019 (Ruff): Binary not found under --strict. "
                    "Install ruff into .venv or PATH."
                )
            return

        try:
            result = subprocess.run(
                [binary, "check", "--output-format", "concise", *self._tool_targets()],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                for line in (result.stdout or "").splitlines():
                    line_str = line.strip()
                    if line_str and not line_str.startswith(("Found ", "[*]")):
                        self.violations.append(f"LINT019 (Ruff): {line_str}")
        except Exception as exc:
            self.violations.append(f"LINT019 (Ruff): Execution error: {exc}")

    def _check_policy_modules_and_engine(self, specs: tuple[Any, ...], protocol: type) -> None:
        check_policy_modules_and_engine(specs, protocol, self.violations)

    def _check_sensor_coverage(self, recipes: tuple[Any, ...], known_sensors: set[str]) -> None:
        check_sensor_coverage(recipes, known_sensors, self.violations)

    def _is_mtf_package(self) -> bool:
        resolved = self.root_dir.resolve()
        return resolved.name == self.manifest.package_name and (resolved / "__init__.py").is_file()

    def _check_subsystem_registry(self) -> None:
        if not self._is_mtf_package():
            return
        check_subsystem_registry(self.root_dir, self.violations)


def print_subsystem_registry() -> None:
    repo_root = str(Path.cwd())
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from strategies.mtf_sniper.directional.setup_recipes import (
        SUBSYSTEM_REGISTRY,
        all_live_recipes,
    )

    recipe_map = {r.name: r for r in all_live_recipes(include_waterfall=True)}
    print("MTF Sniper Registered Directional Sub-Systems:")
    for spec in SUBSYSTEM_REGISTRY:
        rec = recipe_map.get(spec.recipe_name)
        sensors = ", ".join(sorted(rec.required_sensors)) if rec else "MISSING"
        prio = rec.priority if rec is not None else -1
        policy_cls = spec.policy_module.rsplit(".", 1)[-1]
        print(
            f"  - {spec.subsystem_id:14s} | side={spec.side:5s} | prio={prio:3d} | "
            f"policy={policy_cls:20s} | recipe={spec.recipe_name:22s} | sensors=[{sensors}]"
        )


def _run_strategy_tests() -> int:
    venv_pytest = Path(".venv/bin/pytest")
    cmd = [str(venv_pytest) if venv_pytest.is_file() else sys.executable]
    if not venv_pytest.is_file():
        cmd.extend(["-m", "pytest"])
    cmd.extend(["-q", "tests/strategy/"])
    return subprocess.run(cmd, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="MTF Sniper Architectural Guardrail CLI")
    parser.add_argument("target_dir", nargs="?", default="strategies/mtf_sniper")
    parser.add_argument(
        "--subsystems",
        action="store_true",
        help="Print registered directional sub-systems and run all guardrails",
    )
    parser.add_argument(
        "--verify-all",
        action="store_true",
        help="Run --subsystems guardrails (AST + Pyrefly + Ruff, strict) AND tests/strategy/",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if Pyrefly or Ruff binaries are unavailable instead of skipping",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Run fast pure-Python AST, topology, anti-slop, registry, and doc-sync checks",
    )
    args = parser.parse_args()
    if args.subsystems or args.verify_all:
        print_subsystem_registry()
    linter = ArchitectureGuardrailLinter(
        args.target_dir,
        strict_tools=bool(args.strict or args.verify_all),
        skip_external_tools=bool(args.fast),
    )
    rc = linter.run()
    if rc != 0:
        return rc
    if args.verify_all:
        return _run_strategy_tests()
    return 0


if __name__ == "__main__":
    sys.exit(main())
