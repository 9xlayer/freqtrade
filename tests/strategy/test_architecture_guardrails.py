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


if __name__ == "__main__":
    unittest.main()
