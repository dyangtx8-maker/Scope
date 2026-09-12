"""Checks that Phase 2 architecture is wired correctly."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.analyzer import analyze_problem
from agent.models import choose_tier, extract_code, provider_for
from agent.orchestrator import Deadline
from agent.config import CHEAP_PROVIDER, CHEAP_MODEL
from agent.graph import build_graph
from agent.tools import BenchmarkSolutionTool, ToolContext, maybe_run
from agent.verifier import looks_like_stub, spec_cases, syntax_check, verify


SAMPLE = ROOT / "samples" / "1ba0d34fae43f1d26c14f3091598291f0ca9d6ab3f88232d15a3322c017042c3.json"


class ArchitectureTests(unittest.TestCase):
    def test_analyzer_finds_traps(self) -> None:
        problem = json.loads(SAMPLE.read_text())
        analysis = analyze_problem(problem)
        self.assertEqual(analysis.entrypoint, "simulate_writes")
        self.assertEqual(analysis.target_language, "python")
        self.assertTrue(analysis.huge_bounds)
        self.assertEqual(analysis.difficulty, "hard")
        self.assertIn("dict", analysis.return_hint)
        self.assertGreaterEqual(len(spec_cases(analysis)), 3)

    def test_rust_sample_targets_rust(self) -> None:
        rust_problem = None
        for path in sorted((ROOT / "samples").glob("*.json")):
            data = json.loads(path.read_text())
            if data.get("language") == "rust":
                rust_problem = data
                break
        self.assertIsNotNone(rust_problem)
        analysis = analyze_problem(rust_problem)
        self.assertEqual(analysis.target_language, "rust")
        self.assertEqual(analysis.entrypoint, "main")
        self.assertEqual(analysis.proto_entrypoint, "solve")
        self.assertEqual(analysis.io_mode, "stdin")

    def test_extract_rust_fence(self) -> None:
        text = "ok\n```rust\nfn main() {}\n```\n"
        self.assertIn("fn main", extract_code(text, "rust"))

    def test_deadline_counts_down(self) -> None:
        deadline = Deadline(300)
        self.assertGreater(deadline.remaining(), 200)
        self.assertFalse(deadline.late())

    def test_routing_escalates_on_hard_repair(self) -> None:
        self.assertEqual(choose_tier("plan", "hard", 200), "cheap")
        self.assertEqual(choose_tier("generate", "hard", 200), "strong")
        self.assertEqual(choose_tier("repair", "hard", 200, repair_attempt=1), "strong")
        self.assertEqual(choose_tier("generate", "hard", 20), "normal")

    def test_benchmark_needs_analysis(self) -> None:
        ctx = ToolContext(code="def x():\n    return 1\n", remaining_s=120)
        result = maybe_run(BenchmarkSolutionTool(), ctx)
        self.assertFalse(result.used)
        self.assertIn("analysis", result.reason_skipped)

    def test_benchmark_skipped_when_failing(self) -> None:
        ctx = ToolContext(code="def x():\n    return 1\n", remaining_s=120, last_failure=object())
        result = maybe_run(BenchmarkSolutionTool(), ctx)
        self.assertFalse(result.used)

    def test_stub_is_rejected(self) -> None:
        stub = (
            "def simulate_writes(*args):\n"
            "    raise NotImplementedError('generator fallback')\n"
        )
        ok, _ = looks_like_stub(stub)
        self.assertTrue(ok)
        problem = json.loads(SAMPLE.read_text())
        analysis = analyze_problem(problem)
        report = verify(stub, analysis, [])
        self.assertFalse(report.ok)
        self.assertTrue(report.stub)

    def test_verifier_catches_syntax_and_missing_entry(self) -> None:
        ok, _ = syntax_check("def broken(:\n    pass\n")
        self.assertFalse(ok)
        problem = json.loads(SAMPLE.read_text())
        analysis = analyze_problem(problem)
        report = verify("def other():\n    return 1\n", analysis, [])
        self.assertFalse(report.ok)
        self.assertTrue(any("simulate_writes" in hint for hint in report.hints))


    def test_cheap_tier_is_gemini(self) -> None:
        self.assertEqual(provider_for("cheap"), "gemini")
        self.assertEqual(CHEAP_PROVIDER, "gemini")
        self.assertIn("flash-lite", CHEAP_MODEL)
        self.assertEqual(provider_for("normal"), "groq")
        self.assertEqual(provider_for("strong"), "groq")

    def test_langgraph_compiles(self) -> None:
        compiled = build_graph()
        self.assertTrue(hasattr(compiled, "invoke"))
        self.assertIn("analyze", compiled.nodes)
        self.assertIn("verify", compiled.nodes)
        self.assertIn("repair", compiled.nodes)
        self.assertIn("write", compiled.nodes)


if __name__ == "__main__":
    unittest.main()
