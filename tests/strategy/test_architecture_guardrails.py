"""
Automated Architectural Guardrails & Lint Verifier Test Suite
=============================================================
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
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.lint_guardrails import ArchitectureGuardrailLinter


class TestArchitectureGuardrails(unittest.TestCase):
    def test_mtf_sniper_codebase_clean(self):
        linter = ArchitectureGuardrailLinter("strategies/mtf_sniper")
        ret = linter.run()
        violations = linter.violations
        if violations:
            msg = f"\nFound {len(violations)} architectural lint violation(s):\n" + "\n".join(
                f"  - {v}" for v in violations
            )
            self.fail(msg)
        self.assertEqual(ret, 0)

    def test_linter_detects_vietnamese_comment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample = Path(tmpdir) / "sample.py"
            sample.write_text("# Kiểm tra tín hiệu vào lệnh\nx = 1\n", encoding="utf-8")
            linter = ArchitectureGuardrailLinter(tmpdir)
            linter.run()
            self.assertTrue(any("LINT012" in v for v in linter.violations))

    def test_linter_detects_lookahead_shift(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample = Path(tmpdir) / "sample.py"
            sample.write_text("next_close = df['close'].shift(-1)\n", encoding="utf-8")
            linter = ArchitectureGuardrailLinter(tmpdir)
            linter.run()
            self.assertTrue(any("LINT010" in v for v in linter.violations))

    def test_linter_detects_silent_except_pass(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample = Path(tmpdir) / "sample.py"
            sample.write_text("try:\n    x = 1\nexcept Exception:\n    pass\n", encoding="utf-8")
            linter = ArchitectureGuardrailLinter(tmpdir)
            linter.run()
            self.assertTrue(any("LINT011" in v for v in linter.violations))

    def test_linter_detects_workaround_hack_comment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample = Path(tmpdir) / "sample.py"
            sample.write_text("# temporary bypass workaround\nx = 1\n", encoding="utf-8")
            linter = ArchitectureGuardrailLinter(tmpdir)
            linter.run()
            self.assertTrue(any("LINT001" in v for v in linter.violations))

    def test_linter_detects_redundant_type_cast(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample = Path(tmpdir) / "sample.py"
            sample.write_text("def foo(bar: str):\n    return str(bar)\n", encoding="utf-8")
            linter = ArchitectureGuardrailLinter(tmpdir)
            linter.run()
            self.assertTrue(any("LINT014" in v for v in linter.violations))

    def test_linter_detects_core_layer_leakage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            core_dir = Path(tmpdir) / "core"
            core_dir.mkdir(parents=True)
            sample = core_dir / "sample.py"
            sample.write_text(
                "from strategies.mtf_sniper.risk import RiskEngine\n", encoding="utf-8"
            )
            linter = ArchitectureGuardrailLinter(tmpdir)
            linter.run()
            self.assertTrue(any("LINT007" in v for v in linter.violations))

    def test_linter_detects_unfrozen_contract_dataclass(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            core_dir = Path(tmpdir) / "core"
            core_dir.mkdir(parents=True)
            sample = core_dir / "contracts.py"
            sample.write_text(
                "from dataclasses import dataclass\n\n"
                "@dataclass\nclass UnfrozenContract:\n    name: str\n",
                encoding="utf-8",
            )
            linter = ArchitectureGuardrailLinter(tmpdir)
            linter.run()
            self.assertTrue(any("LINT016" in v for v in linter.violations))

    def test_linter_detects_sensor_not_inheriting_base_sensor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sensor_dir = Path(tmpdir) / "signals" / "sensors"
            sensor_dir.mkdir(parents=True)
            sample = sensor_dir / "bad_sensor.py"
            sample.write_text("class RogueSensor:\n    pass\n", encoding="utf-8")
            linter = ArchitectureGuardrailLinter(tmpdir)
            linter.run()
            self.assertTrue(any("LINT018" in v for v in linter.violations))

    def test_linter_detects_signal_and_directional_boundary_violations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sig_dir = Path(tmpdir) / "signals"
            sensor_dir = sig_dir / "sensors"
            dir_dir = Path(tmpdir) / "directional"
            sensor_dir.mkdir(parents=True)
            dir_dir.mkdir(parents=True)

            (sig_dir / "bad_signal.py").write_text(
                "from strategies.mtf_sniper.risk import RiskEngine\n", encoding="utf-8"
            )
            (sensor_dir / "bad_sensor.py").write_text(
                "col = 'enter_short'\n", encoding="utf-8"
            )
            (dir_dir / "setup_composer.py").write_text(
                "class C:\n    def f(self, rs):\n        self._by_side = {r.side: r for r in rs}\n",
                encoding="utf-8",
            )
            (dir_dir / "bad_policy.py").write_text(
                "from strategies.mtf_sniper.execution import EntryPipeline\n",
                encoding="utf-8",
            )

            linter = ArchitectureGuardrailLinter(tmpdir)
            linter.run()
            for code in ("LINT020", "LINT021", "LINT022", "LINT023"):
                self.assertTrue(any(code in v for v in linter.violations), code)

    def test_linter_detects_missing_engine_dispatch_and_duplicate_policy(self):
        from dataclasses import dataclass

        with tempfile.TemporaryDirectory() as tmpdir:
            dir_dir = Path(tmpdir) / "directional"
            exec_dir = Path(tmpdir) / "execution"
            dir_dir.mkdir(parents=True)
            exec_dir.mkdir(parents=True)
            (dir_dir / "engine.py").write_text(
                "class DirectionalTradingEngine:\n    pass\n", encoding="utf-8"
            )
            (exec_dir / "entry_pipeline.py").write_text(
                "def run():\n    return {}\n", encoding="utf-8"
            )
            linter = ArchitectureGuardrailLinter(tmpdir)
            linter.run()
            self.assertEqual(sum("LINT026" in v for v in linter.violations), 2)

        @dataclass(frozen=True)
        class _FakeSpec:
            subsystem_id: str
            policy_module: str

        from strategies.mtf_sniper.directional.setup_recipes import DirectionalPolicyProtocol

        linter = ArchitectureGuardrailLinter("strategies/mtf_sniper")
        linter.violations.clear()
        linter._check_policy_modules_and_engine(
            (
                _FakeSpec("a", "strategies.mtf_sniper.directional.short.policy.ShortPolicy"),
                _FakeSpec("b", "strategies.mtf_sniper.directional.short.policy.ShortPolicy"),
            ),
            DirectionalPolicyProtocol,
        )
        self.assertTrue(any("LINT025" in v for v in linter.violations))

    def test_linter_detects_unregistered_subsystem_and_missing_sensor(self):
        import ast
        from dataclasses import dataclass

        linter = ArchitectureGuardrailLinter("strategies/mtf_sniper")
        linter.violations.clear()
        tree = ast.parse('feats = {"subsystem": "unregistered_hack"}')
        linter._check_pipeline_subsystem_values(
            tree, Path("entry_pipeline.py"), {"short_sniper", "long_sniper", "waterfall"}
        )
        self.assertTrue(any("LINT027" in v for v in linter.violations))

        @dataclass(frozen=True)
        class _FakeRecipe:
            name: str
            required_sensors: frozenset[str]

        linter.violations.clear()
        linter._check_sensor_coverage(
            (_FakeRecipe("setup:orphan", frozenset({"nonexistent_sensor"})),),
            {"waterfall_drop"},
        )
        self.assertTrue(any("LINT028" in v for v in linter.violations))

    def test_linter_detects_fast_path_bypass_lint029(self):
        import ast

        linter = ArchitectureGuardrailLinter("strategies/mtf_sniper")
        linter.violations.clear()
        tree = ast.parse(
            "def _candidates_from_sniper_masks():\n"
            "    return TradeCandidate()\n"
        )
        linter._check_pipeline_single_truth(tree, Path("entry_pipeline.py"))
        self.assertGreaterEqual(sum("LINT029" in v for v in linter.violations), 2)

    def test_strict_tools_fails_when_binary_missing(self):
        from unittest.mock import patch

        linter = ArchitectureGuardrailLinter("strategies/mtf_sniper", strict_tools=True)
        with (
            patch.object(ArchitectureGuardrailLinter, "_find_pyrefly_binary", return_value=None),
            patch.object(ArchitectureGuardrailLinter, "_find_ruff_binary", return_value=None),
        ):
            linter._check_pyrefly()
            linter._check_ruff()
        self.assertTrue(any("LINT015" in v for v in linter.violations))
        self.assertTrue(any("LINT019" in v for v in linter.violations))

    def test_signals_layer_blocks_all_directional_imports_zero_carveouts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sensor_dir = Path(tmpdir) / "signals" / "sensors"
            sensor_dir.mkdir(parents=True)
            (sensor_dir / "pattern_sniper_parts.py").write_text(
                "from strategies.mtf_sniper.directional.short.setups import (\n"
                "    SHORT_SNIPER_PART_NAMES,\n"
                ")\n",
                encoding="utf-8",
            )
            (Path(tmpdir) / "signals" / "candidate_builder.py").write_text(
                "def load():\n"
                "    from ..directional import setup_recipes\n"
                "    return setup_recipes\n",
                encoding="utf-8",
            )
            linter = ArchitectureGuardrailLinter(tmpdir, skip_external_tools=True)
            linter.run()
            self.assertTrue(any("LINT020" in v for v in linter.violations))
            self.assertGreaterEqual(sum("LINT030" in v for v in linter.violations), 2)

    def test_layer_manifest_blocks_horizontal_and_facade_bypass_lint030(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "risk").mkdir()
            (root / "telemetry").mkdir()
            (root / "risk" / "bad_risk.py").write_text(
                "from strategies.mtf_sniper.execution.entry_pipeline import EntryPipeline\n",
                encoding="utf-8",
            )
            (root / "telemetry" / "bad_ledger.py").write_text(
                "from strategies.mtf_sniper.risk.risk_engine import RiskEngine\n",
                encoding="utf-8",
            )
            (root / "strategy_mtf_sniper.py").write_text(
                "from strategies.mtf_sniper.directional.engine import DirectionalTradingEngine\n",
                encoding="utf-8",
            )
            linter = ArchitectureGuardrailLinter(tmpdir, skip_external_tools=True)
            linter.run()
            self.assertEqual(sum("LINT030" in v for v in linter.violations), 3)

    def test_topology_blocks_unapproved_dirs_slop_filenames_and_stray_docs_lint031(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg = Path(tmpdir) / "mtf_sniper"
            (pkg / "utils").mkdir(parents=True)
            (pkg / "signals").mkdir(parents=True)
            (pkg / "docs").mkdir(parents=True)
            (pkg / "__init__.py").write_text("x = 1\n", encoding="utf-8")
            (pkg / "rogue_root.py").write_text("x = 1\n", encoding="utf-8")
            (pkg / "signals" / "policy_v2.py").write_text("x = 1\n", encoding="utf-8")
            (pkg / "signals" / "NOTES.md").write_text("# stray doc\n", encoding="utf-8")
            codes = " ".join(f"LINT{i:03d}" for i in range(1, 37))
            for doc in (
                "README.md",
                "ARCHITECTURE.md",
                "DATA_CONTRACTS.md",
                "PHILOSOPHY.md",
                "GUARDRAILS.md",
            ):
                (pkg / "docs" / doc).write_text(
                    f"4.7.0-1w-regime `anomaly` `pattern` `context` {codes}\n",
                    encoding="utf-8",
                )
            linter = ArchitectureGuardrailLinter(str(pkg), skip_external_tools=True)
            linter._check_subsystem_registry = lambda: None  # type: ignore[method-assign]
            linter.run()
            self.assertGreaterEqual(sum("LINT031" in v for v in linter.violations), 4)

    def test_anti_placeholder_and_stub_guard_lint032_allows_abc_and_protocol(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "core"
            root.mkdir()
            (root / "clean_contracts.py").write_text(
                "from abc import ABC, abstractmethod\n"
                "from typing import Protocol\n\n"
                "class Port(Protocol):\n"
                "    def run(self) -> int: ...\n\n"
                "class Base(ABC):\n"
                "    @abstractmethod\n"
                "    def compute(self) -> int:\n"
                "        raise NotImplementedError\n",
                encoding="utf-8",
            )
            linter = ArchitectureGuardrailLinter(tmpdir, skip_external_tools=True)
            linter.run()
            self.assertFalse(any("LINT032" in v for v in linter.violations))

            (root / "slop_stub.py").write_text(
                "# TODO: implement later\n"
                "def unfinished() -> None:\n"
                "    pass\n\n"
                "class Concrete:\n"
                "    def missing(self) -> int:\n"
                "        raise NotImplementedError('stub')\n",
                encoding="utf-8",
            )
            linter.run()
            self.assertEqual(sum("LINT032" in v for v in linter.violations), 3)

    def test_suppression_and_dynamic_exec_guard_lint033(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "core"
            root.mkdir()
            (root / "specific_ok.py").write_text(
                "x: int = 1  # type: ignore[assignment]\n"
                "y: int = 2  # noqa: F841\n",
                encoding="utf-8",
            )
            linter = ArchitectureGuardrailLinter(tmpdir, skip_external_tools=True)
            linter.run()
            self.assertFalse(any("LINT033" in v for v in linter.violations))

            (root / "blanket_bad.py").write_text(
                "a = 1  # type: ignore\n"
                "b = 2  # noqa\n"
                "c = eval('1 + 1')\n",
                encoding="utf-8",
            )
            linter.run()
            self.assertEqual(sum("LINT033" in v for v in linter.violations), 3)

    def test_hot_path_print_and_dataframe_row_iteration_guard_lint034(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "market"
            root.mkdir()
            (root / "slow_slop.py").write_text(
                "def scan(df):\n"
                "    print('debug')\n"
                "    for _ in df.iterrows():\n"
                "        return 1\n"
                "    return df.apply(lambda r: r['close'], axis=1)\n",
                encoding="utf-8",
            )
            linter = ArchitectureGuardrailLinter(tmpdir, skip_external_tools=True)
            linter.run()
            self.assertEqual(sum("LINT034" in v for v in linter.violations), 3)

    def test_size_budget_and_mutable_defaults_lint035(self):
        from scripts.guardrails.manifest import ArchitectureManifest

        tight_manifest = ArchitectureManifest(max_subsystem_file_lines=10, max_function_lines=5)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "core"
            root.mkdir()
            body_lines = "\n".join(f"    v{i} = {i}" for i in range(10))
            (root / "bloated.py").write_text(
                f"def bloated(items=[]):\n{body_lines}\n    return v0\n",
                encoding="utf-8",
            )
            linter = ArchitectureGuardrailLinter(
                tmpdir,
                skip_external_tools=True,
                manifest=tight_manifest,
            )
            linter.run()
            self.assertEqual(sum("LINT035" in v for v in linter.violations), 3)

    def test_docs_contract_sync_and_recipe_priority_guards_lint024_lint036(self):
        from dataclasses import dataclass

        from scripts.guardrails.anti_slop_rules import check_docs_contract_sync
        from scripts.guardrails.registry_rules import validate_recipe_and_specs

        @dataclass(frozen=True)
        class _Rec:
            name: str
            required_sensors: frozenset[str]
            side: str
            subsystem: str
            priority: int

        @dataclass(frozen=True)
        class _Spec:
            subsystem_id: str
            side: str
            recipe_name: str

        violations: list[str] = []
        validate_recipe_and_specs(
            (
                _Rec("r1", frozenset({"s1"}), "short", "sub1", 10),
                _Rec("r2", frozenset({"s2"}), "short", "sub2", 10),
            ),
            (_Spec("sub1", "short", "r1"), _Spec("sub2", "short", "r2")),
            violations,
        )
        self.assertTrue(any("LINT024" in v and "priority=10" in v for v in violations))

        with tempfile.TemporaryDirectory() as tmpdir:
            pkg = Path(tmpdir) / "mtf_sniper"
            (pkg / "docs").mkdir(parents=True)
            (pkg / "docs" / "README.md").write_text("4.7.0-1w-regime\n", encoding="utf-8")
            (pkg / "docs" / "ARCHITECTURE.md").write_text(
                "4.7.0-1w-regime HunterEscalationGateway\n", encoding="utf-8"
            )
            (pkg / "docs" / "DATA_CONTRACTS.md").write_text(
                "4.7.0-1w-regime `anomaly` `pattern`\n", encoding="utf-8"
            )
            (pkg / "docs" / "PHILOSOPHY.md").write_text("4.6.0-stale\n", encoding="utf-8")
            (pkg / "docs" / "GUARDRAILS.md").write_text(
                "4.7.0-1w-regime LINT001\n", encoding="utf-8"
            )
            doc_violations: list[str] = []
            check_docs_contract_sync(pkg, doc_violations)
            self.assertEqual(sum("LINT036" in v for v in doc_violations), 4)


if __name__ == "__main__":
    unittest.main()
