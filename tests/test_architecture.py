"""Checks that the Phase 4 architecture is wired correctly."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import config, models
from agent.analyzer import analyze_problem
from agent.config import CHEAP_MODEL, CHEAP_PROVIDER, NORMAL_MODEL, STRONG_MODEL
from agent.models import (
    build_argv,
    call_timeout,
    chat,
    choose_tier,
    effort_for,
    extract_code,
    model_chain,
    parse_result,
    payload_error,
    provider_for,
    reset_usage,
)
from agent.orchestrator import Deadline, solve_problem
from agent.graph import build_graph
from solve import main as solve_main
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


    def test_every_tier_is_a_claude_model_on_the_cli(self) -> None:
        for tier in ("cheap", "normal", "strong"):
            self.assertEqual(provider_for(tier), "claude-cli")
        self.assertEqual(CHEAP_PROVIDER, "claude-cli")
        self.assertIn("haiku", CHEAP_MODEL)
        self.assertIn("sonnet", NORMAL_MODEL)
        self.assertIn("opus", STRONG_MODEL)

    def test_model_chain_starts_at_the_tier_and_degrades(self) -> None:
        chain = model_chain("strong")
        self.assertEqual(chain[0], STRONG_MODEL)
        self.assertEqual(chain[-1], CHEAP_MODEL)
        self.assertEqual(len(chain), len(set(chain)))
        self.assertEqual(model_chain("cheap")[0], CHEAP_MODEL)

    def test_effort_steps_down_near_the_deadline(self) -> None:
        self.assertEqual(effort_for("strong", 300), "high")
        self.assertEqual(effort_for("strong", 60), "medium")
        self.assertEqual(effort_for("cheap", 10), "low")

    def test_call_timeout_protects_the_write_reserve(self) -> None:
        self.assertEqual(call_timeout(config.RESERVE_S), 0.0)
        self.assertEqual(call_timeout(config.RESERVE_S + 10), 10.0)
        self.assertEqual(call_timeout(10_000), config.CLI_TIMEOUT_S)

    def test_cli_call_is_hermetic(self) -> None:
        argv = build_argv(model=NORMAL_MODEL, system="sys", effort="medium")
        # Popen gets the resolved path, never the bare name: on Windows the
        # thing on PATH is claude.cmd and CreateProcess ignores PATHEXT.
        self.assertEqual(argv[0], models.cli_path() or config.CLAUDE_BIN)
        for flag in ("--print", "--strict-mcp-config", "--safe-mode", "--no-session-persistence"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertEqual(argv[argv.index("--model") + 1], NORMAL_MODEL)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--permission-prompts") + 1], "none")
        self.assertNotIn("--resume", argv)
        self.assertIn("--session-id", argv)

    def test_resume_forks_into_a_session_id_we_chose(self) -> None:
        argv = build_argv(
            model=NORMAL_MODEL,
            system="sys",
            effort="low",
            resume="old-session",
            session_id="new-session",
            resumable=True,
        )
        self.assertEqual(argv[argv.index("--resume") + 1], "old-session")
        self.assertIn("--fork-session", argv)
        self.assertEqual(argv[argv.index("--session-id") + 1], "new-session")
        self.assertNotIn("--no-session-persistence", argv)

    def test_env_file_values_drop_inline_comments(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# a whole-line comment\n"
                "CLAUDE_CLI_TIMEOUT_S=120     # hard cap for one CLI call\n"
                'export CLAUDE_NORMAL_MODEL="claude-sonnet-5"   # quoted\n'
                "ANTHROPIC_API_KEY='sk-ant-a#b'\n"
                "CLAUDE_CLI_TOOLS=\n"
            )
            env = config.load_env(path)
        self.assertEqual(env["CLAUDE_CLI_TIMEOUT_S"], "120")
        self.assertEqual(env["CLAUDE_NORMAL_MODEL"], "claude-sonnet-5")
        # A '#' inside a quoted value is data, not a comment.
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-ant-a#b")
        self.assertEqual(env["CLAUDE_CLI_TOOLS"], "")
        self.assertEqual(config.load_env(Path(tmp) / "missing.env"), {})

    def test_a_bad_number_falls_back_instead_of_crashing(self) -> None:
        self.assertEqual(config._number("CLAUDE_CLI_ATTEMPTS", "2", int), 2)
        os.environ["CHALLENGEBOX_TEST_NUMBER"] = "not-a-number"
        try:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                value = config._number("CHALLENGEBOX_TEST_NUMBER", "7", int)
        finally:
            os.environ.pop("CHALLENGEBOX_TEST_NUMBER", None)
        self.assertEqual(value, 7)
        self.assertIn("not a number", err.getvalue())

    def test_cli_pipes_are_pinned_to_utf8(self) -> None:
        """Locale codecs mangle the CLI's UTF-8 on Windows (cp1252)."""
        captured = {}

        class FakePopen:
            def __init__(self, argv, **kwargs):
                captured.update(kwargs)
                raise FileNotFoundError("not really spawning")

        real = models.subprocess.Popen
        models.subprocess.Popen = FakePopen
        try:
            payload, error = models._invoke(["claude"], "prompt", 30.0)
        finally:
            models.subprocess.Popen = real
        self.assertIsNone(payload)
        self.assertIn("cannot execute", error)
        self.assertEqual(captured.get("encoding"), "utf-8")
        self.assertEqual(captured.get("errors"), "replace")

    def test_env_file_survives_bom_and_codepage(self) -> None:
        import codecs
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            # A UTF-8 BOM used to be glued onto the first key name.
            path.write_bytes(codecs.BOM_UTF8 + b"ANTHROPIC_API_KEY=sk-bom\n")
            self.assertEqual(config.load_env(path)["ANTHROPIC_API_KEY"], "sk-bom")
            # PowerShell 5.1's `>` writes UTF-16LE, which is not UTF-8 at all.
            path.write_bytes(codecs.BOM_UTF16_LE + "CLAUDE_BIN=x.cmd\n".encode("utf-16-le"))
            self.assertEqual(config.load_env(path)["CLAUDE_BIN"], "x.cmd")
            # An editor-saved code page file keeps its characters.
            path.write_bytes("CLAUDE_CLI_CWD=C:\\M\u00fcller\n".encode("cp1252"))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(config.load_env(path)["CLAUDE_CLI_CWD"], "C:\\M\u00fcller")

    def test_a_node_failure_still_writes_a_solution(self) -> None:
        """A crash in one stage must not cost the whole problem."""
        import tempfile

        from agent import graph as graph_module

        boom = AttributeError("module 'os' has no attribute 'killpg'")
        real_generate = graph_module.generate_python
        real_available = models.cli_available
        graph_module.generate_python = lambda *a, **k: (_ for _ in ()).throw(boom)
        models.cli_available = lambda *a, **k: False
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with contextlib.redirect_stdout(io.StringIO()):
                    meta = solve_problem(str(SAMPLE), out_dir=Path(tmp))
                written = sorted(p.name for p in Path(tmp).glob("*"))
        finally:
            graph_module.generate_python = real_generate
            models.cli_available = real_available
        self.assertEqual(len(written), 2, written)
        self.assertFalse(meta["verified"])

    def test_a_graph_failure_salvages_the_best_candidate(self) -> None:
        """Whatever the guards cannot absorb still reaches disk."""
        import tempfile

        from agent import orchestrator as orch

        class Exploding:
            def invoke(self, state):
                state["ctx"].code = "def keep_me():\n    return 1\n"
                raise AttributeError("module 'os' has no attribute 'killpg'")

        real = orch.compiled_graph
        orch.compiled_graph = lambda: Exploding()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with contextlib.redirect_stdout(io.StringIO()):
                    meta = solve_problem(str(SAMPLE), out_dir=Path(tmp))
                solution = (Path(tmp) / f"{SAMPLE.stem}.py").read_text(encoding="utf-8")
        finally:
            orch.compiled_graph = real
        self.assertTrue(meta["salvaged"])
        self.assertFalse(meta["verified"])
        self.assertIn("killpg", meta["error"])
        self.assertIn("AttributeError", meta["traceback"])
        self.assertIn("def keep_me", solution)

    def test_exit_code_reports_a_run_that_wrote_nothing(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = solve_main(["/nonexistent-problem.json", "--out", tmp])
        self.assertEqual(code, 3)
        self.assertIn("SALVAGED", out.getvalue())

    def test_windows_prefers_the_launchable_shim(self) -> None:
        self.assertEqual(models.bin_candidates("claude", windows=False), ["claude"])
        self.assertEqual(
            models.bin_candidates("claude", windows=True),
            ["claude.cmd", "claude.exe", "claude.bat", "claude"],
        )
        # An explicit path with an extension is taken as given.
        self.assertEqual(
            models.bin_candidates(r"C:\tools\claude.cmd", windows=True),
            [r"C:\tools\claude.cmd"],
        )

    def test_only_sessions_we_minted_can_be_resumed(self) -> None:
        self.assertFalse(models.resumable_session(""))
        self.assertFalse(models.resumable_session("a-session-from-a-parent-process"))

    def test_parse_result_skips_noise_lines(self) -> None:
        stdout = (
            "[claude-code:unrecognized_model] {\"model\":\"x\"}\n"
            '{"type":"result","subtype":"success","is_error":false,"result":"hi",'
            '"session_id":"s1","total_cost_usd":0.5,'
            '"modelUsage":{"claude-sonnet-5":{"inputTokens":10,"outputTokens":3}}}\n'
        )
        payload = parse_result(stdout)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["result"], "hi")
        self.assertEqual(payload_error(payload), "")
        self.assertIsNone(parse_result("not json at all"))

    def test_payload_error_catches_a_soft_api_failure(self) -> None:
        payload = {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "api_error_status": 404,
            "result": "model not found",
        }
        self.assertIn("api_error", payload_error(payload))
        self.assertIn("terminal_reason", payload_error({"terminal_reason": "api_error"}))

    def test_chat_falls_back_locally_when_the_cli_is_missing(self) -> None:
        reset_usage()
        original = models.cli_available
        models.cli_available = lambda *a, **k: False
        try:
            reply = chat(
                [{"role": "user", "content": "hi"}],
                tier="normal",
                remaining_s=300,
                fallback_text="def solve():\n    return 1\n",
            )
        finally:
            models.cli_available = original
            usage = models.USAGE.to_dict()
            reset_usage()
        self.assertEqual(reply.provider, "local")
        self.assertTrue(reply.fallback)
        self.assertIn("def solve", reply.text)
        self.assertEqual(usage["cost_usd"], 0.0)
        self.assertEqual(usage["by_provider"], {"local": 1})

    def test_reply_reads_tokens_cost_and_structured_output(self) -> None:
        payload = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": '{"approach":"x"}',
            "structured_output": {"approach": "x"},
            "session_id": "s1",
            "total_cost_usd": 0.25,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "modelUsage": {
                "claude-opus-5": {
                    "inputTokens": 11,
                    "outputTokens": 7,
                    "cacheReadInputTokens": 3,
                    "cacheCreationInputTokens": 5,
                }
            },
        }
        reply = models._reply_from_payload(payload, "claude-sonnet-5", "strong")
        # modelUsage wins over a zeroed top-level usage block, and it also
        # reveals that the CLI served a different model than we asked for.
        self.assertEqual(reply.model, "claude-opus-5")
        self.assertEqual((reply.prompt_tokens, reply.completion_tokens), (11, 7))
        self.assertEqual((reply.cache_read_tokens, reply.cache_creation_tokens), (3, 5))
        self.assertEqual(reply.cost_usd, 0.25)
        self.assertEqual(reply.provider, "claude-cli")
        self.assertEqual(models.reply_json(reply), {"approach": "x"})

    def test_chat_skips_the_cli_when_the_clock_is_short(self) -> None:
        reset_usage()
        reply = chat(
            [{"role": "user", "content": "hi"}],
            tier="cheap",
            remaining_s=config.RESERVE_S + 1,
            fallback_text="local",
        )
        reset_usage()
        self.assertEqual(reply.provider, "local")
        self.assertEqual(reply.text, "local")

    def test_langgraph_compiles(self) -> None:
        compiled = build_graph()
        self.assertTrue(hasattr(compiled, "invoke"))
        self.assertIn("analyze", compiled.nodes)
        self.assertIn("verify", compiled.nodes)
        self.assertIn("repair", compiled.nodes)
        self.assertIn("write", compiled.nodes)


if __name__ == "__main__":
    unittest.main()
