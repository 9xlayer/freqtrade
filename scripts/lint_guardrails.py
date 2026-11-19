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

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


class ArchitectureGuardrailLinter:
    def __init__(self, root_dir: str = "strategies/mtf_sniper"):
        self.root_dir = Path(root_dir)
        self.violations: list[str] = []
        self.vn_pattern = re.compile(
            r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]",
            re.IGNORECASE,
        )

    def run(self) -> int:
        self.violations.clear()
        self._check_comment_policy()
        self._check_ast_rules()
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

    def _check_misc_node(self, node: ast.AST, py_path: Path) -> None:
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

            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    self._check_import_node(node, rel_str, py_path)
                elif isinstance(node, ast.ClassDef):
                    self._check_class_node(node, rel_str, py_path)
                elif isinstance(node, ast.Assign):
                    self._check_assign_node(node, rel_str, py_path)
                else:
                    self._check_misc_node(node, py_path)

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

    def _check_pyrefly(self) -> None:
        binary = self._find_pyrefly_binary()
        if not binary:
            return

        try:
            target = str(self.root_dir)
            result = subprocess.run(
                [binary, "check", target],
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
            return

        try:
            target = str(self.root_dir)
            result = subprocess.run(
                [binary, "check", "--output-format", "concise", target],
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


if __name__ == "__main__":
    target_dir = sys.argv[1] if len(sys.argv) > 1 else "strategies/mtf_sniper"
    linter = ArchitectureGuardrailLinter(target_dir)
    sys.exit(linter.run())
