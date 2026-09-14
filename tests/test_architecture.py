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
from agent.planner import plan_solution
from agent.graph import build_graph
from solve import _outcome as outcome
from solve import _solve_one as solve_worker
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
            self.assertTrue(models.model_for(tier).startswith("claude-"))
        self.assertEqual(CHEAP_PROVIDER, "claude-cli")
        # Every stage runs Opus by default.
        for model in (CHEAP_MODEL, NORMAL_MODEL, STRONG_MODEL):
            self.assertIn("opus", model)

    def test_every_chain_keeps_a_rung_below_the_primary(self) -> None:
        """A one-model chain sends a timeout straight to the local skeleton."""
        for tier in ("cheap", "normal", "strong"):
            chain = model_chain(tier)
            self.assertEqual(chain[0], models.model_for(tier))
            self.assertGreaterEqual(len(chain), 2, f"{tier} has no fallback rung")
            self.assertEqual(len(chain), len(set(chain)))
            self.assertNotEqual(chain[-1], chain[0])

    def test_first_model_cannot_spend_the_whole_budget(self) -> None:
        """The window each attempt gets, so a fallback still has a clock."""
        windows = []
        real_invoke = models._invoke
        models._invoke = lambda argv, prompt, timeout: (windows.append(timeout), (None, "timeout"))[1]
        real_available = models.cli_available
        models.cli_available = lambda *a, **k: True
        real_attempts = config.CLI_ATTEMPTS
        config.CLI_ATTEMPTS = 1
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                chat([{"role": "user", "content": "hi"}], tier="strong",
                     timeout_s=200.0, fallback_text="local")
        finally:
            models._invoke = real_invoke
            models.cli_available = real_available
            config.CLI_ATTEMPTS = real_attempts
            reset_usage()
        self.assertGreaterEqual(len(windows), 2, windows)
        # the first model gets a share, never the lot
        self.assertLess(windows[0], 200.0)
        self.assertAlmostEqual(windows[0], 200.0 * config.CLI_FIRST_ATTEMPT_SHARE, delta=1.0)
        # and something is left for the next one
        self.assertGreater(windows[1], config.CLI_MIN_CALL_S)

    def test_prompt_log_records_argv_and_both_prompts(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prompts.log"
            config.PROMPT_LOG = str(path)
            try:
                models._log_call(
                    ["claude", "--model", "claude-opus-5", "--effort", "high"],
                    "SYSTEM TEXT", "USER TEXT",
                    {"result": "REPLY TEXT", "total_cost_usd": 0.25}, "", 1.5,
                )
            finally:
                config.PROMPT_LOG = ""
            written = path.read_text(encoding="utf-8")
        for fragment in ("claude-opus-5", "high", "SYSTEM TEXT", "USER TEXT",
                         "REPLY TEXT", "$0.2500"):
            self.assertIn(fragment, written)

    def test_prompt_log_off_by_default_and_never_raises(self) -> None:
        self.assertEqual(config.PROMPT_LOG, "")
        config.PROMPT_LOG = "/nonexistent-dir/cannot/write.log"
        try:
            models._log_call(["claude"], "s", "u", None, "boom", 0.1)  # must not raise
        finally:
            config.PROMPT_LOG = ""

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

    def test_parallel_mode_fans_generate_and_tests_out_of_plan(self) -> None:
        def edges(parallel):
            return {
                (e.source, e.target)
                for e in build_graph(parallel).get_graph().edges
                if e.source in {"plan", "generate", "tests"}
            }

        self.assertEqual(
            edges(False), {("plan", "generate"), ("generate", "tests"), ("tests", "verify")}
        )
        self.assertEqual(
            edges(True),
            {
                ("plan", "generate"),
                ("plan", "tests"),
                ("generate", "verify"),
                ("tests", "verify"),
            },
        )

    def test_both_graph_shapes_produce_the_same_run(self) -> None:
        """Fanning out must not drop, duplicate or reorder any event."""
        import tempfile

        from agent import generator, graph as graph_module

        real_available = models.cli_available
        real_sleep = generator.time.sleep
        models.cli_available = lambda *a, **k: False  # local fallbacks: fast and free
        generator.time.sleep = lambda _s: None
        runs = {}
        try:
            for parallel in (False, True):
                config.PARALLEL_STAGES = parallel
                graph_module._COMPILED.clear()
                with tempfile.TemporaryDirectory() as tmp:
                    with contextlib.redirect_stdout(io.StringIO()):
                        runs[parallel] = solve_problem(str(SAMPLE), out_dir=Path(tmp))
        finally:
            models.cli_available = real_available
            generator.time.sleep = real_sleep
            config.PARALLEL_STAGES = True
            graph_module._COMPILED.clear()

        for parallel, meta in runs.items():
            stages = [e["stage"] for e in meta["events"]]
            self.assertEqual(stages[:6], ["analyze", "analyze", "plan", "generate", "tests", "verify"], parallel)
            self.assertEqual(stages[-1], "write", parallel)
            self.assertEqual(len(meta["repairs"]), config.MAX_REPAIRS, parallel)
        self.assertEqual(
            [e["stage"] for e in runs[False]["events"]],
            [e["stage"] for e in runs[True]["events"]],
        )

    def test_worker_body_captures_output_and_never_raises(self) -> None:
        record = solve_worker("/nonexistent-problem.json", "/tmp")
        self.assertIsNotNone(record["meta"])  # salvaged rather than raised
        self.assertTrue(record["meta"]["salvaged"])
        self.assertIn("run failed", record["log"])  # the log came back, not stdout

    def test_outcome_classifies_every_ending(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(outcome(Path("p"), None, "boom"), "crashed")
            self.assertEqual(outcome(Path("p"), {"salvaged": True}, ""), "salvaged")
            self.assertEqual(outcome(Path("p"), {"verified": True}, ""), "ok")
            self.assertEqual(outcome(Path("p"), {"verified": False}, ""), "unverified")

    def test_plan_llm_defaults_on_and_is_a_real_setting(self) -> None:
        # Setting config.PLAN_LLM inside a test creates the attribute, so the
        # switch can look wired while the module ships no default at all.
        self.assertIn("PLAN_LLM", vars(config))
        self.assertIs(config.PLAN_LLM, True)

    def test_plan_llm_off_skips_the_model_entirely(self) -> None:
        problem = json.loads(SAMPLE.read_text())
        analysis = analyze_problem(problem)
        called = []
        from agent import planner

        real_chat = planner.chat
        planner.chat = lambda *a, **k: called.append(1)
        config.PLAN_LLM = False
        try:
            plan = plan_solution(analysis, 300.0)
        finally:
            planner.chat = real_chat
            config.PLAN_LLM = True
        self.assertEqual(called, [])  # no CLI call at all
        self.assertEqual(plan.model, "local-heuristic")
        self.assertTrue(plan.fallback)
        self.assertEqual(plan.cost_usd, 0.0)
        # the analyzer-derived fields still arrive
        self.assertIn("simulate_writes", plan.python_signature)
        self.assertTrue(plan.traps_to_handle)

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
