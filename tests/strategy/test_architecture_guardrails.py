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


if __name__ == "__main__":
    unittest.main()
