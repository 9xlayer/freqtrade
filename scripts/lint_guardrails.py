#!/usr/bin/env python3
"""

Command: python3 scripts/lint_guardrails.py strategies/mtf_sniper

Architectural Linting & Guardrail Verifier
=========================================
Enforces AGENT OPERATING PRINCIPLES & VERIFICATION STANDARDS:
- Determinism Over Prose
- Codebase Hygiene (No workarounds, hacks, dead paths)
- 99% No-Comment Policy (Self-documenting code, max comment density < 5%)
- Strict Architectural Boundaries (Directional isolation, sensor one-way flow)
- JEV Mode & Model Hygiene
- Anti-Lookahead Bias (No negative shift)
- Silent Exception Swallowing Guard (No bare except: pass)
- 100% English Codebase (Rule 2 enforcement)
- Positional Indexing Integrity (Anti-duplicate index bug)
- Redundant Type Cast Guard (LINT014)
- Pyrefly Static Type & Signature Enforcement (LINT015)
- Immutable Inter-Layer Contracts (LINT016)
- Facade Thinness & Delegation Purity (LINT017)
- Concrete Sensor OOP Inheritance (LINT018)
- Ruff Modernization & Complexity Enforcement (LINT019)
"""

from __future__ import annotations

import argparse
import ast
import importlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


class ArchitectureGuardrailLinter:
    def __init__(
        self,
        root_dir: str = "strategies/mtf_sniper",
        *,
        strict_tools: bool = False,
    ):
        self.root_dir = Path(root_dir)
        self.strict_tools = strict_tools
        self.violations: list[str] = []
        self.vn_pattern = re.compile(
            r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]",
            re.IGNORECASE,
        )

    def run(self) -> int:
        self.violations.clear()
        self._check_comment_policy()
        self._check_ast_rules()
        self._check_subsystem_registry()
        self._check_pyrefly()
        self._check_ruff()

        if not self.violations:
            print("OK: All architectural guardrails and lint rules passed cleanly (0 violations).")
            return 0

        print(f"FAILED: Found {len(self.violations)} architectural guardrail violation(s):\n")
        for v in self.violations:
            print(f"  [X] {v}")
        print(
            "\nRemediation: Resolve root cause, adhere to Type system and architectural boundaries."
        )
        return 1

    def _check_comment_policy(self) -> None:
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

        for py_path in self.root_dir.rglob("*.py"):
            total_lines = 0
            comment_lines = 0
            with py_path.open(encoding="utf-8", errors="ignore") as f:
                for line_no, line in enumerate(f, 1):
                    total_lines += 1
                    s = line.strip()

                    # LINT012: English-only guard (Rule 2)
                    if py_path.name != "lint_guardrails.py" and self.vn_pattern.search(line):
                        self.violations.append(
                            f"{py_path}:{line_no} LINT012: "
                            "Non-English text detected in code/comment. "
                            "Enforce Rule 2: 100% English in codebase."
                        )

                    if s.startswith("#"):
                        comment_lines += 1
                        for pattern, msg in prohibited_words:
                            if pattern.search(s):
                                self.violations.append(f"{py_path}:{line_no} {msg}")

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

    def _check_directional_imports(
        self, module_name: str, rel_str: str, lineno: int, py_path: Path
    ) -> None:
        if "directional/long" in rel_str and "directional.short" in module_name:
            self.violations.append(
                f"{py_path}:{lineno} LINT003: LongPolicy cross-imports Short ({module_name}). "
                "Directional trading paths must remain strictly isolated."
            )
        if "directional/short" in rel_str and "directional.long" in module_name:
            self.violations.append(
                f"{py_path}:{lineno} LINT003: ShortPolicy cross-imports Long ({module_name}). "
                "Directional trading paths must remain strictly isolated."
            )

    def _check_signals_directional_imports(
        self, module_name: str, rel_str: str, lineno: int, py_path: Path
    ) -> None:
        if rel_str.startswith(("signals/", "signals\\")):
            for forbidden in ("execution", "risk", "intelligence", "strategy_mtf_sniper"):
                if f"mtf_sniper.{forbidden}" in module_name or f".{forbidden}" in module_name:
                    self.violations.append(
                        f"{py_path}:{lineno} LINT020: "
                        f"Signals layer cannot import upward from '{forbidden}' ({module_name}). "
                        "Sensors emit isolated TriggerEvents only."
                    )
            is_legacy_parts = py_path.name == "pattern_sniper_parts.py"
            if (
                ("signals/sensors" in rel_str or "signals\\sensors" in rel_str)
                and not is_legacy_parts
                and ("mtf_sniper.directional" in module_name or ".directional" in module_name)
            ):
                self.violations.append(
                    f"{py_path}:{lineno} LINT020: "
                    f"Concrete sensor cannot import directional policy/recipes ({module_name})."
                )

        if rel_str.startswith(("directional/", "directional\\")):
            for forbidden in ("execution", "risk", "intelligence", "strategy_mtf_sniper"):
                if f"mtf_sniper.{forbidden}" in module_name or f".{forbidden}" in module_name:
                    self.violations.append(
                        f"{py_path}:{lineno} LINT023: "
                        f"Directional layer cannot import '{forbidden}' ({module_name}). "
                        "Directional subsystems evaluate signals/policy only."
                    )

    def _check_layer_isolation(
        self, module_name: str, rel_str: str, lineno: int, py_path: Path
    ) -> None:
        if "signals/sensors" in rel_str and (
            "execution" in module_name or "strategy_mtf_sniper" in module_name
        ):
            self.violations.append(
                f"{py_path}:{lineno} LINT004: "
                "Sensor module cannot import upward from execution pipeline or strategy."
            )
        self._check_signals_directional_imports(module_name, rel_str, lineno, py_path)

        forbidden_upper = (
            "signals",
            "directional",
            "execution",
            "risk",
            "intelligence",
            "strategy_mtf_sniper",
        )
        if "core/" in rel_str:
            for forbidden in ("market", *forbidden_upper):
                if f"mtf_sniper.{forbidden}" in module_name or f".{forbidden}" in module_name:
                    self.violations.append(
                        f"{py_path}:{lineno} LINT007: "
                        f"Core domain cannot import higher layer '{forbidden}' ({module_name}). "
                        "Layer 0 must remain strictly independent."
                    )

        if "market/" in rel_str:
            for forbidden in forbidden_upper:
                if f"mtf_sniper.{forbidden}" in module_name or f".{forbidden}" in module_name:
                    self.violations.append(
                        f"{py_path}:{lineno} LINT008: "
                        f"Market module cannot import higher layer '{forbidden}' ({module_name}). "
                        "Market intelligence must remain pure and stateless."
                    )

    def _check_facade_imports(self, module_name: str, lineno: int, py_path: Path) -> None:
        if py_path.name != "strategy_mtf_sniper.py" and "strategy_mtf_sniper" in module_name:
            self.violations.append(
                f"{py_path}:{lineno} LINT009: "
                f"Internal subsystem cannot import Strategy facade ({module_name}). "
                "Prevents circular dependencies back to the Freqtrade entrypoint."
            )
        if py_path.name == "strategy_mtf_sniper.py" and (
            "talib" in module_name or "technical" in module_name
        ):
            self.violations.append(
                f"{py_path}:{lineno} LINT017: "
                f"Strategy facade cannot import indicator libraries directly ({module_name}). "
                "Delegate technical computing to market/indicators.py or sensors."
            )

    def _check_import_node(self, node: ast.AST, rel_str: str, py_path: Path) -> None:
        module_name = ""
        if isinstance(node, ast.ImportFrom) and node.module:
            module_name = node.module
        elif isinstance(node, ast.Import) and node.names:
            module_name = node.names[0].name
        if not module_name:
            return

        lineno = getattr(node, "lineno", 0)
        self._check_directional_imports(module_name, rel_str, lineno, py_path)
        self._check_layer_isolation(module_name, rel_str, lineno, py_path)
        self._check_facade_imports(module_name, lineno, py_path)

    def _check_class_node(self, node: ast.ClassDef, rel_str: str, py_path: Path) -> None:
        if "core/contracts.py" in rel_str:
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
            "signals/sensors" in rel_str
            and node.name.endswith("Sensor")
            and node.name != "BaseSensor"
        ):
            base_names = [b.id for b in node.bases if isinstance(b, ast.Name)]
            if "BaseSensor" not in base_names:
                self.violations.append(
                    f"{py_path}:{node.lineno} LINT018: "
                    f"Concrete sensor '{node.name}' must inherit from BaseSensor."
                )

    def _check_assign_node(self, node: ast.Assign, rel_str: str, py_path: Path) -> None:
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
                "execution/entry_pipeline.py" in rel_str
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
                "directional/setup_composer.py" in rel_str
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
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
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
                if (
                    isinstance(k, ast.Constant)
                    and k.value == "subsystem"
                    and v is not None
                ):
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
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
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

    def _check_misc_node(self, node: ast.AST, py_path: Path, rel_str: str = "") -> None:
        if (
            ("signals/sensors" in rel_str or "signals\\sensors" in rel_str)
            and isinstance(node, ast.Constant)
            and node.value in ("enter_long", "enter_short", "exit_long", "exit_short")
        ):
            self.violations.append(
                f"{py_path}:{node.lineno} LINT021: "
                f"Sensor cannot emit/mutate trade column '{node.value}'. "
                "Return TriggerEvents only."
            )
        if (
            "directional/setup_composer.py" in rel_str
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

        for py_path in self.root_dir.rglob("*.py"):
            try:
                with py_path.open(encoding="utf-8", errors="ignore") as f:
                    tree = ast.parse(f.read(), filename=str(py_path))
                    parsed_trees[py_path] = tree
                    class_fields.update(self._collect_class_fields(tree))
            except Exception as e:
                self.violations.append(f"{py_path}: LINT999: Failed to parse AST: {e}")

        for py_path, tree in parsed_trees.items():
            rel_str = str(py_path.relative_to(self.root_dir))
            if rel_str.replace("\\", "/") == "directional/engine.py":
                self._check_engine_dispatch_ast(tree, py_path)
            elif rel_str.replace("\\", "/") == "execution/entry_pipeline.py":
                self._check_pipeline_subsystem_tag_ast(tree, py_path)

            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    self._check_import_node(node, rel_str, py_path)
                elif isinstance(node, ast.ClassDef):
                    self._check_class_node(node, rel_str, py_path)
                elif isinstance(node, ast.Assign):
                    self._check_assign_node(node, rel_str, py_path)
                else:
                    self._check_misc_node(node, py_path, rel_str)

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
        self_script = Path("scripts/lint_guardrails.py")
        if self._is_mtf_package() and self_script.is_file():
            targets.append(str(self_script))
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

    def _validate_recipe_and_specs(self, recipes: tuple, specs: tuple) -> None:
        recipe_map = {r.name: r for r in recipes}
        if len(recipe_map) != len(recipes):
            self.violations.append("LINT024: Duplicate SetupRecipe.name in all_live_recipes().")
        for r in recipes:
            if r.side not in ("long", "short") or not r.required_sensors or not r.subsystem:
                self.violations.append(
                    f"LINT024: Recipe '{r.name}' has invalid side/sensors/subsystem."
                )
        for spec in specs:
            rec = recipe_map.get(spec.recipe_name)
            if rec is None or rec.side != spec.side or rec.subsystem != spec.subsystem_id:
                self.violations.append(
                    f"LINT024: Subsystem '{spec.subsystem_id}' recipe mismatch: "
                    f"{spec.recipe_name!r}."
                )

    def _check_policy_modules_and_engine(self, specs: tuple, protocol: type) -> None:
        seen_modules: dict[str, str] = {}
        for spec in specs:
            prev = seen_modules.get(spec.policy_module)
            if prev is not None:
                self.violations.append(
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
                    self.violations.append(
                        f"LINT025: Policy '{spec.policy_module}' for '{spec.subsystem_id}' "
                        "does not satisfy DirectionalPolicyProtocol."
                    )
            except Exception as exc:
                self.violations.append(
                    f"LINT025: Failed to load policy '{spec.policy_module}' for "
                    f"'{spec.subsystem_id}': {exc}"
                )

    def _check_engine_wiring(self, specs: tuple, protocol: type) -> None:
        from strategies.mtf_sniper.directional.engine import DirectionalTradingEngine

        engine = DirectionalTradingEngine()
        policies = getattr(engine, "subsystem_policies", None)
        if not isinstance(policies, dict):
            self.violations.append(
                "LINT026: DirectionalTradingEngine.subsystem_policies dict is missing."
            )
            return
        for spec in specs:
            pol = policies.get(spec.subsystem_id)
            if pol is None or not isinstance(pol, protocol):
                self.violations.append(
                    f"LINT026: DirectionalTradingEngine.subsystem_policies missing valid "
                    f"protocol implementation for '{spec.subsystem_id}'."
                )

    def _is_mtf_package(self) -> bool:
        resolved = self.root_dir.resolve()
        return resolved.name == "mtf_sniper" and (resolved / "__init__.py").is_file()

    def _check_sensor_coverage(self, recipes: tuple, known_sensors: set[str]) -> None:
        for rec in recipes:
            missing = sorted(set(rec.required_sensors) - known_sensors)
            if missing:
                self.violations.append(
                    f"LINT028: Recipe '{rec.name}' references missing sensor(s) "
                    f"{missing} not implemented in signals/sensors/."
                )

    def _check_subsystem_registry(self) -> None:
        """LINT024-028: Validate sub-systems, dedicated policies, sensors, and dispatch."""
        if not self._is_mtf_package():
            return
        try:
            from types import SimpleNamespace

            pkg_root = str(self.root_dir.resolve().parent.parent)
            if pkg_root not in sys.path:
                sys.path.insert(0, pkg_root)
            from strategies.mtf_sniper.directional.setup_recipes import (
                SUBSYSTEM_REGISTRY,
                DirectionalPolicyProtocol,
                all_live_recipes,
            )
            from strategies.mtf_sniper.signals.sensor_suite import SensorSuite
        except Exception as exc:
            self.violations.append(f"LINT024: Failed to load subsystem registry: {exc}")
            return

        recipes = all_live_recipes(include_waterfall=True)
        self._validate_recipe_and_specs(recipes, SUBSYSTEM_REGISTRY)
        self._check_policy_modules_and_engine(SUBSYSTEM_REGISTRY, DirectionalPolicyProtocol)
        self._check_engine_wiring(SUBSYSTEM_REGISTRY, DirectionalPolicyProtocol)

        dummy_strat = SimpleNamespace(custom_pair_params={})
        known_sensors = {
            *(s.name for s in SensorSuite.live(dummy_strat).sensors),
            *(s.name for s in SensorSuite.full().sensors),
        }
        self._check_sensor_coverage(recipes, known_sensors)


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
        policy_cls = spec.policy_module.rsplit(".", 1)[-1]
        print(
            f"  - {spec.subsystem_id:14s} | side={spec.side:5s} | "
            f"policy={policy_cls:20s} | recipe={spec.recipe_name:22s} | sensors=[{sensors}]"
        )


def _run_strategy_tests() -> int:
    venv_pytest = Path(".venv/bin/pytest")
    cmd = [str(venv_pytest) if venv_pytest.is_file() else sys.executable]
    if not venv_pytest.is_file():
        cmd.extend(["-m", "pytest"])
    cmd.extend(["-q", "tests/strategy/"])
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
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
    args = parser.parse_args()
    if args.subsystems or args.verify_all:
        print_subsystem_registry()
    linter = ArchitectureGuardrailLinter(
        args.target_dir,
        strict_tools=bool(args.strict or args.verify_all),
    )
    rc = linter.run()
    if rc != 0:
        sys.exit(rc)
    if args.verify_all:
        sys.exit(_run_strategy_tests())
    sys.exit(0)
