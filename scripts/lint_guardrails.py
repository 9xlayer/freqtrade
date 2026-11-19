#!/usr/bin/env python3
"""
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
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import List, Optional


class ArchitectureGuardrailLinter:
    def __init__(self, root_dir: str = "strategies/mtf_sniper"):
        self.root_dir = Path(root_dir)
        self.violations: List[str] = []
        self.vn_pattern = re.compile(
            r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]",
            re.IGNORECASE,
        )

    def run(self) -> int:
        self.violations.clear()
        self._check_comment_policy()
        self._check_ast_rules()

        if not self.violations:
            print("OK: All architectural guardrails and lint rules passed cleanly (0 violations).")
            return 0

        print(f"FAILED: Found {len(self.violations)} architectural guardrail violation(s):\n")
        for v in self.violations:
            print(f"  [X] {v}")
        print("\nRemediation: Resolve root cause, adhere to Type system and architectural boundaries.")
        return 1

    def _check_comment_policy(self) -> None:
        prohibited_words = [
            (
                re.compile(r"#\s*(workaround|hack|fixme|quickfix|bypass_rule|temporary\s+bypass)", re.IGNORECASE),
                "LINT001: Prohibited workaround/hack comment detected. Resolve root cause instead of using justifications.",
            )
        ]

        for py_path in self.root_dir.rglob("*.py"):
            total_lines = 0
            comment_lines = 0
            with open(py_path, "r", encoding="utf-8", errors="ignore") as f:
                for line_no, line in enumerate(f, 1):
                    total_lines += 1
                    s = line.strip()

                    # LINT012: English-only guard (Rule 2)
                    if py_path.name != "lint_guardrails.py" and self.vn_pattern.search(line):
                        self.violations.append(
                            f"{py_path}:{line_no} LINT012: Non-English text detected in code/comment. "
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
                        f"{py_path}: LINT002: Comment density ({density * 100:.1f}%) exceeds 5.0% threshold. "
                        "Code must be self-documenting (99% No-Comment Policy)."
                    )

    def _check_ast_rules(self) -> None:
        def _get_type_name(t_node: ast.AST) -> Optional[str]:
            if isinstance(t_node, ast.Name):
                return t_node.id
            if isinstance(t_node, ast.Subscript):
                if isinstance(t_node.value, ast.Name) and t_node.value.id == "Optional":
                    if isinstance(t_node.slice, ast.Name):
                        return t_node.slice.id
            return None

        class_fields = {}
        parsed_trees = {}
        for py_path in self.root_dir.rglob("*.py"):
            try:
                with open(py_path, "r", encoding="utf-8", errors="ignore") as f:
                    tree = ast.parse(f.read(), filename=str(py_path))
                    parsed_trees[py_path] = tree
            except Exception as e:
                self.violations.append(f"{py_path}: LINT999: Failed to parse AST: {e}")
                continue

            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    c_types = {}
                    for item in node.body:
                        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                            t = _get_type_name(item.annotation)
                            if t:
                                c_types[item.target.id] = t
                    if c_types:
                        class_fields[node.name] = c_types

        for py_path, tree in parsed_trees.items():
            rel_str = str(py_path.relative_to(self.root_dir))

            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module_name = ""
                    if isinstance(node, ast.ImportFrom) and node.module:
                        module_name = node.module
                    elif isinstance(node, ast.Import) and node.names:
                        module_name = node.names[0].name

                    if "directional/long" in rel_str and "directional.short" in module_name:
                        self.violations.append(
                            f"{py_path}:{node.lineno} LINT003: LongPolicy cross-imports Short module ({module_name}). "
                            "Directional trading paths must remain strictly isolated."
                        )
                    if "directional/short" in rel_str and "directional.long" in module_name:
                        self.violations.append(
                            f"{py_path}:{node.lineno} LINT003: ShortPolicy cross-imports Long module ({module_name}). "
                            "Directional trading paths must remain strictly isolated."
                        )

                    if "signals/sensors" in rel_str and ("execution" in module_name or "strategy_mtf_sniper" in module_name):
                        self.violations.append(
                            f"{py_path}:{node.lineno} LINT004: Sensor module cannot import upward from execution pipeline or strategy."
                        )

                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Subscript):
                            if isinstance(target.value, ast.Attribute) and target.value.attr == "environ":
                                if isinstance(target.slice, ast.Constant) and target.slice.value == "JEV_MODE":
                                    self.violations.append(
                                        f"{py_path}:{node.lineno} LINT005: Direct mutation of os.environ['JEV_MODE'] is prohibited. "
                                        "Use runtime profiles or explicit function arguments."
                                    )

                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if re.match(r"^jev-hunter-[a-zA-Z0-9_-]+$", node.value):
                        self.violations.append(
                            f"{py_path}:{node.lineno} LINT006: Invalid model ID '{node.value}'. "
                            "Only canonical alias 'jev-latest' is permitted under Rule 5."
                        )

                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "shift":
                    if node.args and isinstance(node.args[0], ast.UnaryOp) and isinstance(node.args[0].op, ast.USub):
                        self.violations.append(
                            f"{py_path}:{node.lineno} LINT010: Negative shift (shift(-...)) lookahead bias is strictly prohibited."
                        )

                if isinstance(node, ast.ExceptHandler):
                    is_bare = node.type is None
                    is_broad = isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")
                    if (is_bare or is_broad) and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                        self.violations.append(
                            f"{py_path}:{node.lineno} LINT011: Silent exception swallowing (except Exception: pass) is prohibited. "
                            "Catch specific typed exceptions or log warnings."
                        )

                if "execution/entry_pipeline.py" in rel_str and isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute):
                            if target.value.attr == "loc":
                                self.violations.append(
                                    f"{py_path}:{node.lineno} LINT013: Label assignment via .loc is prohibited in entry_pipeline. "
                                    "Use NumPy boolean masks or positional .iloc[pos] indexing."
                                )

            for func_node in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                var_types = {}
                all_args = func_node.args.args + func_node.args.kwonlyargs
                if func_node.args.vararg:
                    all_args.append(func_node.args.vararg)
                if func_node.args.kwarg:
                    all_args.append(func_node.args.kwarg)
                for arg in all_args:
                    if arg and arg.annotation:
                        t = _get_type_name(arg.annotation)
                        if t:
                            var_types[arg.arg] = t

                for subnode in ast.walk(func_node):
                    if isinstance(subnode, ast.Call) and isinstance(subnode.func, ast.Name):
                        fn_name = subnode.func.id
                        if fn_name in ("str", "int", "float", "bool") and subnode.args:
                            arg = subnode.args[0]
                            if isinstance(arg, ast.Constant):
                                if (fn_name == "str" and isinstance(arg.value, str)) or \
                                   (fn_name == "int" and isinstance(arg.value, int)) or \
                                   (fn_name == "float" and isinstance(arg.value, float)) or \
                                   (fn_name == "bool" and isinstance(arg.value, bool)):
                                    self.violations.append(
                                        f"{py_path}:{subnode.lineno} LINT014: Redundant type cast {fn_name}({arg.value!r}) on literal."
                                    )
                            elif fn_name == "bool" and isinstance(arg, (ast.Compare, ast.UnaryOp, ast.BoolOp)):
                                if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.Not):
                                    self.violations.append(
                                        f"{py_path}:{subnode.lineno} LINT014: Redundant bool(...) call on `not` expression."
                                    )
                                elif isinstance(arg, ast.Compare):
                                    self.violations.append(
                                        f"{py_path}:{subnode.lineno} LINT014: Redundant bool(...) call on comparison expression."
                                    )
                            elif isinstance(arg, ast.Name):
                                v_name = arg.id
                                if v_name in var_types and var_types[v_name] == fn_name:
                                    self.violations.append(
                                        f"{py_path}:{subnode.lineno} LINT014: Redundant type cast {fn_name}({v_name}); "
                                        f"parameter '{v_name}' is already of type '{fn_name}'."
                                    )
                            elif isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
                                obj_name = arg.value.id
                                attr_name = arg.attr
                                if obj_name in var_types:
                                    cls_name = var_types[obj_name]
                                    if cls_name in class_fields and attr_name in class_fields[cls_name]:
                                        if class_fields[cls_name][attr_name] == fn_name:
                                            self.violations.append(
                                                f"{py_path}:{subnode.lineno} LINT014: Redundant type cast {fn_name}({obj_name}.{attr_name}); "
                                                f"attribute '{attr_name}' is already of type '{fn_name}' in {cls_name}."
                                            )


if __name__ == "__main__":
    target_dir = sys.argv[1] if len(sys.argv) > 1 else "strategies/mtf_sniper"
    linter = ArchitectureGuardrailLinter(target_dir)
    sys.exit(linter.run())
