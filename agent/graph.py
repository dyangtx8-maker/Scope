"""LangGraph control flow around the existing solver stages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

from . import config
from .analyzer import Analysis
from .generator import generate_python, generate_rust
from .models import USAGE
from .planner import plan_solution
from .repair import repair_solution
from .tools import (
    AnalyzeConstraintsTool,
    BenchmarkSolutionTool,
    GenerateTestsTool,
    InspectFailureTool,
    ReadProblemTool,
    RunSolutionTool,
    ToolContext,
    maybe_run,
)
from .verifier import VerifyReport, rust_static_check, verify


class AgentState(TypedDict, total=False):
    problem_path: str
    out_dir: str
    deadline: Any
    events: list
    ctx: Any
    analysis: Any
    plan: Any
    code: str
    rust_code: str
    tests: list
    report: Any
    rust_report: Any
    gen_meta: dict
    rust_meta: dict
    repairs: list
    repair_attempt: int
    best_code: str
    best_report: Any
    benchmark: dict
    meta: dict


def _report_score(report: VerifyReport) -> tuple[int, int, int, int]:
    passed = sum(1 for case in report.cases if case.get("ok"))
    return (
        1 if report.ok else 0,
        0 if report.stub else 1,
        1 if report.syntax_ok else 0,
        passed,
    )


def _emit(state: AgentState, stage: str, detail: str, **extra: Any) -> list:
    deadline = state["deadline"]
    event = {
        "stage": stage,
        "elapsed_s": round(deadline.elapsed(), 3),
        "remaining_s": round(deadline.remaining(), 3),
        "detail": detail,
        "extra": extra,
    }
    events = list(state.get("events") or [])
    events.append(event)
    print(
        f"[{event['elapsed_s']:7.2f}s | rem {event['remaining_s']:7.2f}s] "
        f"{stage}: {detail}",
        flush=True,
    )
    return events


def _sync_ctx(state: AgentState) -> ToolContext:
    ctx: ToolContext = state["ctx"]
    ctx.remaining_s = state["deadline"].remaining()
    if "analysis" in state:
        ctx.analysis = state.get("analysis")
    if "plan" in state:
        ctx.plan = state.get("plan")
    if "code" in state:
        ctx.code = state.get("code") or ""
    if "rust_code" in state:
        ctx.rust_code = state.get("rust_code") or ""
    if "tests" in state:
        ctx.tests = list(state.get("tests") or [])
    report = state.get("report")
    ctx.last_failure = None if getattr(report, "ok", True) else report
    return ctx


def node_analyze(state: AgentState) -> dict[str, Any]:
    ctx = _sync_ctx(state)
    read = maybe_run(ReadProblemTool(), ctx)
    events = _emit(state, "analyze", read.summary, used=read.used, node="analyze")
    analyzed = maybe_run(AnalyzeConstraintsTool(), ctx)
    events = _emit(
        {**state, "events": events},
        "analyze",
        analyzed.summary,
        used=analyzed.used,
        node="analyze",
    )
    return {
        "ctx": ctx,
        "analysis": ctx.analysis,
        "events": events,
        "repairs": [],
        "repair_attempt": 0,
        "rust_meta": {},
        "gen_meta": {},
    }


def node_plan(state: AgentState) -> dict[str, Any]:
    ctx = _sync_ctx(state)
    analysis: Analysis = state["analysis"]
    plan = plan_solution(analysis, state["deadline"].remaining())
    ctx.plan = plan
    events = _emit(
        state,
        "plan",
        plan.approach[:160],
        model=plan.model,
        fallback=plan.fallback,
        node="plan",
    )
    return {"ctx": ctx, "plan": plan, "events": events}


def node_generate(state: AgentState) -> dict[str, Any]:
    ctx = _sync_ctx(state)
    code, gen_meta = generate_python(
        state["analysis"], state["plan"], state["deadline"].remaining()
    )
    ctx.code = code
    events = _emit(
        state,
        "generate",
        f"python chars={len(code)} model={gen_meta.get('model')} "
        f"tier={gen_meta.get('tier')} provider={gen_meta.get('provider', '')}",
        node="generate",
        **gen_meta,
    )
    return {
        "ctx": ctx,
        "code": code,
        "best_code": code,
        "gen_meta": gen_meta,
        "events": events,
    }


def node_tests(state: AgentState) -> dict[str, Any]:
    ctx = _sync_ctx(state)
    tests_tool = maybe_run(GenerateTestsTool(), ctx)
    events = _emit(
        state,
        "tests",
        tests_tool.summary,
        used=tests_tool.used,
        node="tests",
    )
    return {"ctx": ctx, "tests": ctx.tests, "events": events}


def _report_from_run(run, code: str, analysis: Analysis, tests: list) -> VerifyReport:
    if run.used and isinstance(run.data, dict):
        return VerifyReport(
            ok=run.ok,
            summary=run.summary,
            syntax_ok=bool(run.data.get("syntax_ok", True)),
            entrypoint_ok=bool(run.data.get("entrypoint_ok", True)),
            stub=bool(run.data.get("stub", False)),
            cases=list(run.data.get("cases") or []),
            hints=list(run.data.get("hints") or []),
        )
    return verify(code, analysis, tests)


def node_verify(state: AgentState) -> dict[str, Any]:
    ctx = _sync_ctx(state)
    run = maybe_run(RunSolutionTool(), ctx)
    report = _report_from_run(run, ctx.code, state["analysis"], ctx.tests)
    ctx.last_failure = None if report.ok else report
    events = _emit(
        state,
        "verify",
        report.summary,
        used=run.used,
        ok=report.ok,
        node="verify",
    )
    best_report = state.get("best_report") or report
    if _report_score(report) >= _report_score(best_report):
        best_code = ctx.code
        best_report = report
    else:
        best_code = state.get("best_code") or ctx.code
    return {
        "ctx": ctx,
        "report": report,
        "best_code": best_code,
        "best_report": best_report,
        "events": events,
    }


def node_repair(state: AgentState) -> dict[str, Any]:
    ctx = _sync_ctx(state)
    inspect = maybe_run(InspectFailureTool(), ctx)
    attempt = int(state.get("repair_attempt") or 0) + 1
    events = _emit(state, "repair", inspect.summary, used=inspect.used, node="repair")
    events = _emit(
        {**state, "events": events},
        "repair",
        f"attempt {attempt}/{config.MAX_REPAIRS}",
        node="repair",
    )
    candidate, repair_meta = repair_solution(
        state["analysis"],
        state["plan"],
        state.get("code") or "",
        state["report"],
        state["deadline"].remaining(),
        attempt=attempt,
    )
    candidate_report = verify(candidate, state["analysis"], state.get("tests") or [])
    best_code = state.get("best_code") or state.get("code") or ""
    best_report = state.get("best_report") or state["report"]
    kept = _report_score(candidate_report) >= _report_score(best_report)
    if kept:
        code, report = candidate, candidate_report
        best_code, best_report = code, report
    else:
        events = _emit(
            {**state, "events": events},
            "repair",
            "rejected worse candidate; kept previous best",
            node="repair",
        )
        code, report = best_code, best_report
    ctx.code = code
    ctx.last_failure = None if report.ok else report
    repairs = list(state.get("repairs") or [])
    repairs.append(
        {
            "attempt": attempt,
            "ok": candidate_report.ok,
            "kept": kept,
            "summary": candidate_report.summary,
            "model": repair_meta.get("model"),
            "tier": repair_meta.get("tier"),
        }
    )
    events = _emit(
        {**state, "events": events},
        "verify",
        report.summary,
        ok=report.ok,
        repair=attempt,
        node="repair",
    )
    return {
        "ctx": ctx,
        "code": code,
        "report": report,
        "best_code": best_code,
        "best_report": best_report,
        "repair_attempt": attempt,
        "repairs": repairs,
        "events": events,
    }


def node_emit_rust(state: AgentState) -> dict[str, Any]:
    ctx = _sync_ctx(state)
    rust_code, rust_meta = generate_rust(
        state["analysis"],
        state["plan"],
        state.get("code") or "",
        state["deadline"].remaining(),
    )
    ctx.rust_code = rust_code
    events = _emit(
        state,
        "emit_rust",
        f"chars={len(rust_code)} model={rust_meta.get('model')}",
        node="emit_rust",
        **rust_meta,
    )
    rust_report = rust_static_check(rust_code)
    deadline = state["deadline"]
    if not rust_report.ok and deadline.can_repair() and not deadline.late():
        rust_code, rust_fix = repair_solution(
            state["analysis"],
            state["plan"],
            rust_code,
            rust_report,
            deadline.remaining(),
            attempt=1,
            language="rust",
        )
        rust_meta["repair"] = rust_fix
        rust_report = rust_static_check(rust_code)
        ctx.rust_code = rust_code
        events = _emit(
            {**state, "events": events},
            "emit_rust",
            rust_report.summary,
            ok=rust_report.ok,
            node="emit_rust",
        )
    return {
        "ctx": ctx,
        "rust_code": rust_code,
        "rust_meta": rust_meta,
        "rust_report": rust_report,
        "events": events,
    }


def node_write(state: AgentState) -> dict[str, Any]:
    ctx = _sync_ctx(state)
    deadline = state["deadline"]
    analysis: Analysis = state["analysis"]
    report: VerifyReport = state.get("best_report") or state["report"]
    code = state.get("best_code") or state.get("code") or ""
    ctx.code = code
    ctx.last_failure = None if report.ok else report
    bench = maybe_run(BenchmarkSolutionTool(), ctx)
    events = _emit(
        state,
        "write",
        bench.summary,
        used=bench.used,
        node="write",
    )
    if deadline.remaining() < config.RESERVE_S:
        events = _emit(
            {**state, "events": events},
            "write",
            "reserve reached; writing best solution so far",
            node="write",
        )

    out_dir = Path(state["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(state["problem_path"]).stem
    rust_report = state.get("rust_report")
    rust_code = state.get("rust_code") or ""
    rust_ok = rust_report.ok if rust_report else True
    verified = report.ok and rust_ok

    proto_path = out_dir / f"{stem}.py"
    proto_header = (
        f"# problem_id: {analysis.problem_id}\n"
        f"# source_language: {analysis.source_language}\n"
        f"# phase: {config.PHASE}\n"
        f"# entrypoint: {analysis.proto_entrypoint or analysis.entrypoint}\n"
        f"# verified: {report.ok}\n\n"
    )
    proto_path.write_text(proto_header + code.rstrip() + "\n", encoding="utf-8")

    if analysis.target_language == "rust" and rust_code:
        solution_path = out_dir / f"{stem}.rs"
        rust_header = (
            f"// problem_id: {analysis.problem_id}\n"
            f"// phase: {config.PHASE}\n"
            f"// entrypoint: main\n"
            f"// verified: {verified}\n\n"
        )
        solution_path.write_text(rust_header + rust_code.rstrip() + "\n", encoding="utf-8")
    else:
        solution_path = proto_path

    meta = {
        "phase": config.PHASE,
        "problem_id": analysis.problem_id,
        "source": state["problem_path"],
        "solution": str(solution_path),
        "prototype": str(proto_path),
        "target_language": analysis.target_language,
        "verified": verified,
        "python_verified": report.ok,
        "rust_verified": rust_ok if rust_report else None,
        "elapsed_s": round(deadline.elapsed(), 3),
        "deadline_s": deadline.total_s,
        "usage": USAGE.to_dict(),
        "analysis": analysis.compact(),
        "plan": state["plan"].to_dict(),
        "generate": state.get("gen_meta") or {},
        "generate_rust": state.get("rust_meta") or {},
        "verify": report.to_dict(),
        "rust_verify": rust_report.to_dict() if rust_report else None,
        "repairs": state.get("repairs") or [],
        "benchmark": bench.data if bench.used else {"used": False, "reason": bench.reason_skipped},
        "events": events,
        "graph": "langgraph",
    }
    meta_path = out_dir / f"{stem}.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    events = _emit(
        {**state, "events": events},
        "write",
        f"{solution_path.name} verified={verified}",
        node="write",
    )
    meta["events"] = events
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"meta": meta, "events": events, "code": code, "report": report}


def _after_verify(state: AgentState) -> str:
    report = state.get("report")
    deadline = state["deadline"]
    attempt = int(state.get("repair_attempt") or 0)
    if (
        report is not None
        and not report.ok
        and attempt < config.MAX_REPAIRS
        and deadline.can_repair()
        and not deadline.late()
    ):
        return "repair"
    analysis = state.get("analysis")
    if (
        analysis is not None
        and analysis.target_language == "rust"
        and deadline.remaining() > 30
    ):
        return "emit_rust"
    return "write"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("analyze", node_analyze)
    graph.add_node("plan", node_plan)
    graph.add_node("generate", node_generate)
    graph.add_node("tests", node_tests)
    graph.add_node("verify", node_verify)
    graph.add_node("repair", node_repair)
    graph.add_node("emit_rust", node_emit_rust)
    graph.add_node("write", node_write)
    graph.set_entry_point("analyze")
    graph.add_edge("analyze", "plan")
    graph.add_edge("plan", "generate")
    graph.add_edge("generate", "tests")
    graph.add_edge("tests", "verify")
    graph.add_conditional_edges(
        "verify",
        _after_verify,
        {"repair": "repair", "emit_rust": "emit_rust", "write": "write"},
    )
    graph.add_edge("repair", "verify")
    graph.add_edge("emit_rust", "write")
    graph.add_edge("write", END)
    return graph.compile()


_COMPILED = None


def compiled_graph():
    global _COMPILED
    if _COMPILED is None:
        _COMPILED = build_graph()
    return _COMPILED
