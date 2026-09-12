"""Generic tool interface. The orchestrator decides what is worth calling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    name: str
    used: bool
    ok: bool
    summary: str
    data: Any = None
    reason_skipped: str = ""


@dataclass
class ToolContext:
    problem_path: str = ""
    problem: dict[str, Any] = field(default_factory=dict)
    analysis: Any = None
    plan: Any = None
    code: str = ""
    rust_code: str = ""
    tests: list[dict[str, Any]] = field(default_factory=list)
    last_failure: Any = None
    remaining_s: float = 0.0
    notes: list[str] = field(default_factory=list)


class Tool:
    name = "tool"

    def should_use(self, ctx: ToolContext) -> tuple[bool, str]:
        return True, "default"

    def run(self, ctx: ToolContext) -> ToolResult:
        raise NotImplementedError


def maybe_run(tool: Tool, ctx: ToolContext) -> ToolResult:
    useful, reason = tool.should_use(ctx)
    if not useful:
        return ToolResult(
            name=tool.name,
            used=False,
            ok=True,
            summary=f"skipped: {reason}",
            reason_skipped=reason,
        )
    result = tool.run(ctx)
    result.used = True
    return result


class ReadProblemTool(Tool):
    name = "read_problem"

    def should_use(self, ctx: ToolContext) -> tuple[bool, str]:
        if not ctx.problem_path:
            return False, "no problem path"
        return True, "need the specification"

    def run(self, ctx: ToolContext) -> ToolResult:
        from pathlib import Path
        import json

        path = Path(ctx.problem_path)
        ctx.problem = json.loads(path.read_text())
        pid = ctx.problem.get("problem_id", path.stem)
        return ToolResult(
            name=self.name,
            used=True,
            ok=True,
            summary=f"loaded {path.name} id={str(pid)[:12]}…",
            data={"keys": sorted(ctx.problem.keys())},
        )


class AnalyzeConstraintsTool(Tool):
    name = "analyze_constraints"

    def should_use(self, ctx: ToolContext) -> tuple[bool, str]:
        if not ctx.problem:
            return False, "problem not loaded"
        return True, "always run local trap detection"

    def run(self, ctx: ToolContext) -> ToolResult:
        from .analyzer import analyze_problem

        ctx.analysis = analyze_problem(ctx.problem)
        traps = len(ctx.analysis.algorithmic_traps) + len(ctx.analysis.wording_traps)
        return ToolResult(
            name=self.name,
            used=True,
            ok=True,
            summary=(
                f"lang={ctx.analysis.target_language} "
                f"entrypoint={ctx.analysis.entrypoint} "
                f"difficulty={ctx.analysis.difficulty} "
                f"huge_bounds={len(ctx.analysis.huge_bounds)} "
                f"traps={traps}"
            ),
            data=ctx.analysis.to_dict(),
        )


class GenerateTestsTool(Tool):
    name = "generate_tests"

    def should_use(self, ctx: ToolContext) -> tuple[bool, str]:
        if ctx.analysis is None:
            return False, "no analysis"
        if ctx.remaining_s < 25:
            return False, "deadline too close for extra tests"
        return True, "spec cases plus adversarial tests when time allows"

    def run(self, ctx: ToolContext) -> ToolResult:
        from .verifier import build_tests, propose_adversarial_tests

        ctx.tests = build_tests(ctx.analysis, ctx.problem)
        extra = propose_adversarial_tests(ctx.analysis, ctx.plan, ctx.remaining_s)
        ctx.tests.extend(extra)
        return ToolResult(
            name=self.name,
            used=True,
            ok=True,
            summary=f"prepared {len(ctx.tests)} tests ({len(extra)} adversarial)",
            data={"count": len(ctx.tests), "names": [t.get("name") for t in ctx.tests]},
        )


class RunSolutionTool(Tool):
    name = "run_solution"

    def should_use(self, ctx: ToolContext) -> tuple[bool, str]:
        if not ctx.code:
            return False, "no code yet"
        if not ctx.tests:
            return False, "no tests to run"
        return True, "execute generated tests locally"

    def run(self, ctx: ToolContext) -> ToolResult:
        from .verifier import run_tests

        report = run_tests(ctx.code, ctx.analysis, ctx.tests)
        if not report.ok:
            ctx.last_failure = report
        else:
            ctx.last_failure = None
        return ToolResult(
            name=self.name,
            used=True,
            ok=report.ok,
            summary=report.summary,
            data=report.to_dict(),
        )


class InspectFailureTool(Tool):
    name = "inspect_failure"

    def should_use(self, ctx: ToolContext) -> tuple[bool, str]:
        if ctx.last_failure is None:
            return False, "no failure to inspect"
        return True, "turn verifier output into repair hints"

    def run(self, ctx: ToolContext) -> ToolResult:
        failure = ctx.last_failure
        hints = list(getattr(failure, "hints", []) or [])
        ctx.notes.extend(hints)
        return ToolResult(
            name=self.name,
            used=True,
            ok=True,
            summary=f"{len(hints)} repair hints",
            data={"hints": hints, "summary": getattr(failure, "summary", "")},
        )


class BenchmarkSolutionTool(Tool):
    name = "benchmark_solution"

    def should_use(self, ctx: ToolContext) -> tuple[bool, str]:
        if not ctx.code:
            return False, "no code"
        if ctx.analysis is None:
            return False, "no analysis"
        if ctx.remaining_s < 80:
            return False, "not enough time for a benchmark"
        if ctx.last_failure is not None:
            return False, "skip benchmark while the solution still fails tests"
        return True, "time a couple of local cases"

    def run(self, ctx: ToolContext) -> ToolResult:
        from .verifier import benchmark

        data = benchmark(ctx.code, ctx.analysis, ctx.tests)
        return ToolResult(
            name=self.name,
            used=True,
            ok=bool(data.get("ok", True)),
            summary=str(data.get("summary") or data.get("reason") or "benchmark"),
            data=data,
        )


def default_tools() -> list[Tool]:
    return [
        ReadProblemTool(),
        AnalyzeConstraintsTool(),
        GenerateTestsTool(),
        RunSolutionTool(),
        InspectFailureTool(),
        BenchmarkSolutionTool(),
    ]
