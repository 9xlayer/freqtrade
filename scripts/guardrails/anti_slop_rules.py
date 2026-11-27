"""Anti-AI-slop rules enforcing topology, stubs, suppressions, runtime speed, and doc sync."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from scripts.guardrails.manifest import DEFAULT_MANIFEST, ArchitectureManifest


_PLACEHOLDER_COMMENT_RE = re.compile(
    r"#\s*(?:todo|tbd|xxx|placeholder|unimplemented|coming\s+soon)\b",
    re.IGNORECASE,
)
_BARE_TYPE_IGNORE_RE = re.compile(r"#\s*type:\s*ignore(?!\s*\[)")
_BARE_NOQA_RE = re.compile(r"#\s*noqa(?!\s*:)")
_ABSTRACT_BASES = frozenset({"ABC", "Protocol"})
_BANNED_ROW_METHODS = frozenset({"iterrows", "itertuples"})
_BANNED_DYNAMIC_CALLS = frozenset({"eval", "exec"})


def check_package_topology(
    root_dir: Path,
    *,
    is_mtf_package: bool,
    violations: list[str],
    manifest: ArchitectureManifest = DEFAULT_MANIFEST,
) -> None:
    """LINT031: Lock directory structure, root files, stray markdown, and slop filenames."""
    if not root_dir.is_dir():
        return

    for path in sorted(root_dir.rglob("*")):
        if "__pycache__" in path.parts or any(part.startswith(".") for part in path.parts):
            continue
        rel_posix = path.relative_to(root_dir).as_posix()

        if path.is_dir():
            if rel_posix not in manifest.allowed_subdirs:
                violations.append(
                    f"{path}: LINT031: Unapproved directory '{rel_posix}'. "
                    "Extend ArchitectureManifest explicitly instead of creating ad-hoc folders."
                )
            continue

        if path.suffix.lower() == ".md" and not rel_posix.startswith("docs/"):
            violations.append(
                f"{path}: LINT031: Markdown file '{rel_posix}' outside 'docs/' is prohibited."
            )

        if path.suffix == ".py" and manifest.slop_filename_regex.search(path.name):
            violations.append(
                f"{path}: LINT031: Prohibited AI-slop filename '{path.name}'. "
                "Modify canonical domain modules instead of creating duplicate/utility files."
            )

        if is_mtf_package and "/" not in rel_posix and path.name not in manifest.allowed_root_files:
            violations.append(
                f"{path}: LINT031: Unapproved root file '{path.name}' in {manifest.package_name}. "
                f"Allowed root files: {sorted(manifest.allowed_root_files)}."
            )


def check_lexical_slop_line(
    line: str,
    stripped: str,
    line_no: int,
    py_path: Path,
    violations: list[str],
) -> None:
    """LINT032 & LINT033: Line-level scan for unfinished TODOs and blanket linter suppressions."""
    if stripped.startswith("#") and _PLACEHOLDER_COMMENT_RE.search(stripped):
        violations.append(
            f"{py_path}:{line_no} LINT032: Unfinished placeholder comment detected. "
            "Implement complete production logic or track backlog in memory-bank/progress.md."
        )
    if _BARE_TYPE_IGNORE_RE.search(line):
        violations.append(
            f"{py_path}:{line_no} LINT033: Blanket '# type: ignore' without '[error-code]' "
            "is prohibited. Fix the static type mismatch or specify the exact error code."
        )
    if _BARE_NOQA_RE.search(line):
        violations.append(
            f"{py_path}:{line_no} LINT033: Blanket '# noqa' without explicit rule IDs "
            "is prohibited. Specify '# noqa: RULE_ID' only when strictly justified."
        )


def _strip_docstring(stmts: list[ast.stmt]) -> list[ast.stmt]:
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        return stmts[1:]
    return stmts


def _has_abstract_decorator(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in func.decorator_list:
        if isinstance(dec, ast.Name) and "abstract" in dec.id:
            return True
        if isinstance(dec, ast.Attribute) and "abstract" in dec.attr:
            return True
    return False


def _is_abstract_class(cls_node: ast.ClassDef) -> bool:
    for base in cls_node.bases:
        if isinstance(base, ast.Name) and base.id in _ABSTRACT_BASES:
            return True
        if isinstance(base, ast.Attribute) and base.attr in _ABSTRACT_BASES:
            return True
    return False


def _is_stub_body(stmts: list[ast.stmt]) -> bool:
    body = _strip_docstring(stmts)
    if len(body) != 1:
        return False
    stmt = body[0]
    if isinstance(stmt, ast.Pass):
        return True
    if (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    ):
        return True
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        exc = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
        if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
            return True
    return False


def _check_function_stub_and_budget(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    in_abstract_cls: bool,
    rel_posix: str,
    py_path: Path,
    violations: list[str],
    manifest: ArchitectureManifest,
) -> None:
    if not in_abstract_cls and not _has_abstract_decorator(func) and _is_stub_body(func.body):
        violations.append(
            f"{py_path}:{func.lineno} LINT032: Concrete function/method '{func.name}' has an "
            "empty pass/ellipsis/NotImplementedError stub body."
        )

    defaults = [*func.args.defaults, *(d for d in func.args.kw_defaults if d is not None)]
    for default_node in defaults:
        if isinstance(default_node, (ast.List, ast.Dict, ast.Set)):
            violations.append(
                f"{py_path}:{func.lineno} LINT035: Mutable default argument in '{func.name}'. "
                "Use None or immutable tuples/frozensets."
            )

    if rel_posix not in manifest.function_line_exempt_files:
        end_line = func.end_lineno or func.lineno
        func_lines = end_line - func.lineno + 1
        if func_lines > manifest.max_function_lines:
            violations.append(
                f"{py_path}:{func.lineno} LINT035: Function '{func.name}' spans {func_lines} "
                f"lines (max {manifest.max_function_lines}). Decompose into focused helpers."
            )


def _is_axis_one_apply(call: ast.Call) -> bool:
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "apply"):
        return False
    return any(
        kw.arg == "axis"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value in (1, "columns")
        for kw in call.keywords
    )


def _check_module_definitions(
    tree: ast.Module,
    rel_posix: str,
    py_path: Path,
    violations: list[str],
    manifest: ArchitectureManifest,
) -> None:
    for item in tree.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _check_function_stub_and_budget(
                item,
                in_abstract_cls=False,
                rel_posix=rel_posix,
                py_path=py_path,
                violations=violations,
                manifest=manifest,
            )
        elif isinstance(item, ast.ClassDef):
            in_abs = _is_abstract_class(item)
            for member in item.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _check_function_stub_and_budget(
                        member,
                        in_abstract_cls=in_abs,
                        rel_posix=rel_posix,
                        py_path=py_path,
                        violations=violations,
                        manifest=manifest,
                    )


def _check_call_anti_slop(node: ast.Call, py_path: Path, violations: list[str]) -> None:
    if isinstance(node.func, ast.Name):
        if node.func.id in _BANNED_DYNAMIC_CALLS:
            violations.append(
                f"{py_path}:{node.lineno} LINT033: Dynamic code execution '{node.func.id}()' "
                "is strictly prohibited."
            )
        elif node.func.id == "print":
            violations.append(
                f"{py_path}:{node.lineno} LINT034: Direct print() in strategy code is "
                "prohibited. Use structured module logging (`logger.*`)."
            )
    elif isinstance(node.func, ast.Attribute) and (
        node.func.attr in _BANNED_ROW_METHODS or _is_axis_one_apply(node)
    ):
        violations.append(
            f"{py_path}:{node.lineno} LINT034: Row-wise DataFrame iteration "
            f"('.{node.func.attr}()') is prohibited on strategy hot paths. "
            "Use vectorized NumPy/Pandas array operations."
        )


def check_ast_anti_slop(
    tree: ast.AST,
    rel_posix: str,
    total_lines: int,
    py_path: Path,
    violations: list[str],
    manifest: ArchitectureManifest = DEFAULT_MANIFEST,
) -> None:
    """LINT032-LINT035: AST checks for stubs, runtime anti-patterns, and module/function bloat."""
    max_lines = manifest.file_line_overrides.get(rel_posix, manifest.max_subsystem_file_lines)
    if total_lines > max_lines:
        violations.append(
            f"{py_path}: LINT035: Module spans {total_lines} lines (budget {max_lines}). "
            "Split responsibilities into cohesive domain modules."
        )

    if isinstance(tree, ast.Module):
        _check_module_definitions(tree, rel_posix, py_path, violations, manifest)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            _check_call_anti_slop(node, py_path, violations)


def _verify_authoritative_doc(
    root_dir: Path,
    rel_doc: str,
    strategy_version: str,
    manifest: ArchitectureManifest,
    violations: list[str],
) -> None:
    doc_path = root_dir / rel_doc
    if not doc_path.is_file():
        violations.append(
            f"{doc_path}: LINT036: Required architecture document '{rel_doc}' is missing."
        )
        return
    content = doc_path.read_text(encoding="utf-8")
    if strategy_version not in content:
        violations.append(
            f"{doc_path}: LINT036: Document version drift — missing current "
            f"STRATEGY_VERSION '{strategy_version}'."
        )
    if rel_doc in ("docs/ARCHITECTURE.md", "docs/DATA_CONTRACTS.md"):
        for dead_sym in manifest.banned_legacy_doc_symbols:
            if dead_sym in content:
                violations.append(
                    f"{doc_path}: LINT036: Document still references removed legacy symbol "
                    f"'{dead_sym}'."
                )


def check_docs_contract_sync(
    root_dir: Path,
    violations: list[str],
    manifest: ArchitectureManifest = DEFAULT_MANIFEST,
) -> None:
    """LINT036: Ensure authoritative docs match STRATEGY_VERSION, SensorType, and active classes."""
    pkg_root = str(root_dir.resolve().parent.parent)
    if pkg_root not in sys.path:
        sys.path.insert(0, pkg_root)

    try:
        from strategies.mtf_sniper import STRATEGY_VERSION
        from strategies.mtf_sniper.core.enums import SensorType
    except Exception as exc:
        violations.append(
            f"{root_dir}: LINT036: Failed to import version/enums for doc sync: {exc}"
        )
        return

    for rel_doc in manifest.authoritative_docs:
        _verify_authoritative_doc(root_dir, rel_doc, STRATEGY_VERSION, manifest, violations)

    contracts_doc = root_dir / "docs/DATA_CONTRACTS.md"
    if contracts_doc.is_file():
        text = contracts_doc.read_text(encoding="utf-8")
        for member in SensorType:
            if f"`{member.value}`" not in text:
                violations.append(
                    f"{contracts_doc}: LINT036: SensorType value '{member.value}' is missing "
                    "from docs/DATA_CONTRACTS.md."
                )

    guardrails_doc = root_dir / "docs/GUARDRAILS.md"
    if guardrails_doc.is_file():
        g_text = guardrails_doc.read_text(encoding="utf-8")
        missing_codes = [c for c in manifest.required_guardrail_codes if c not in g_text]
        if missing_codes:
            violations.append(
                f"{guardrails_doc}: LINT036: Missing rule code(s) in docs/GUARDRAILS.md: "
                f"{missing_codes}."
            )
