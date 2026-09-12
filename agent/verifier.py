"""Local verification: stubs, expected values, trap tests, optional rustc."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any

from . import config
from .analyzer import Analysis
from .models import chat, choose_tier, extract_json
from .planner import Plan


@dataclass
class VerifyReport:
    ok: bool
    summary: str
    syntax_ok: bool = False
    entrypoint_ok: bool = False
    stub: bool = False
    cases: list[dict[str, Any]] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_STUB_MARKERS = (
    "NotImplementedError",
    "Placeholder solution",
    "Phase 1 Python stand-in",
    "prototype not generated",
    "generator fallback",
    "current task is to provide a syntactically correct",
)


def syntax_check(code: str) -> tuple[bool, str]:
    try:
        ast.parse(code)
        return True, "syntax ok"
    except SyntaxError as exc:
        return False, f"syntax error: {exc}"


def looks_like_stub(code: str, language: str = "python") -> tuple[bool, str]:
    lowered = code.lower()
    for marker in _STUB_MARKERS:
        if marker.lower() in lowered:
            return True, f"stub marker: {marker}"
    if language == "python" and len(code) < 450:
        if "return (stdin or" in code or "return text.strip()" in code:
            return True, "echo stub"
        if code.count("return") <= 1 and "raise NotImplementedError" in code:
            return True, "unimplemented stub"
    if language == "rust":
        if "fn main" not in code:
            return True, "missing fn main"
        if "unsafe" in lowered:
            return True, "unsafe is not allowed"
        body = code.split("fn main", 1)[-1]
        if "println" not in body and "write" not in body and "print!" not in body:
            if len(code) < 400:
                return True, "rust program does not produce output"
    return False, ""


def _norm(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    return value


def spec_cases(analysis: Analysis) -> list[dict[str, Any]]:
    """Tiny expected cases taken from the wording, not from hidden tests."""
    name = analysis.proto_entrypoint or analysis.entrypoint
    if name == "simulate_writes":
        zero_tel = {
            "sent": 0, "dropped": 0, "errors": 0, "invalid": 0,
            "attempts": 0, "spins": 0, "yields": 0,
            "final_level": 0, "final_credits": 0,
        }
        return [
            {
                "name": "spec_empty",
                "args": [[], [], 0, 0, 1, 0, 0],
                "expected": {"packets": [], "telemetry": dict(zero_tel)},
                "kind": "spec",
            },
            {
                "name": "spec_invalid",
                "args": [[0, 65536], [], 0, 0, 1, 0, 0],
                "expected": {
                    "packets": [["INVALID", 0, 0, 0], ["INVALID", 0, 0, 0]],
                    "telemetry": {**zero_tel, "invalid": 2},
                },
                "kind": "spec",
            },
            {
                "name": "spec_implicit_error",
                "args": [[5], [], 0, 0, 1, 0, 0],
                "expected": {
                    "packets": [["ERROR", 0, 0, 0]],
                    "telemetry": {**zero_tel, "errors": 1, "attempts": 1},
                },
                "kind": "spec",
            },
            {
                "name": "spec_one_ok",
                "args": [[3], [("ok", 1)], 0, 0, 1, 0, 10],
                "expected": {
                    "packets": [["SENT", 0, 0, 0]],
                    "telemetry": {**zero_tel, "sent": 1, "attempts": 1, "final_credits": 3},
                },
                "kind": "spec",
            },
        ]
    if name == "track_indicator":
        return [
            {"name": "spec_no_ops_empty", "args": [[], -1, []], "expected": [], "kind": "spec"},
            {"name": "spec_insert_empty", "args": [[], -1, [("insert", 0, [7, 8])]], "expected": [0], "kind": "spec"},
        ]
    if name == "validate_build":
        return [
            {
                "name": "spec_unclosed",
                "args": [[(("field", "a", "int"),)], 0, []],
                "expected": 1,
                "kind": "spec",
            }
        ]
    if name == "normalize_protection":
        return [{"name": "spec_empty", "args": [[], [], 10, 10], "expected": [], "kind": "spec"}]
    if name == "refresh_references":
        return [{"name": "spec_empty", "args": [[], [], []], "expected": [], "kind": "spec"}]
    return []


def build_tests(analysis: Analysis, problem: dict[str, Any]) -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    for i, example in enumerate(analysis.public_examples):
        if isinstance(example, dict) and "args" in example:
            tests.append(
                {
                    "name": f"public_{i}",
                    "args": example.get("args") or [],
                    "expected": example.get("expected"),
                    "kind": "public",
                }
            )
    tests.extend(spec_cases(analysis))

    if analysis.io_mode == "stdin":
        tests.append({"name": "empty_stdin", "stdin": "", "kind": "smoke", "allow_empty": True})
        if analysis.huge_bounds:
            tests.append({"name": "trap_no_huge_loop", "kind": "static_trap"})
        return tests

    if analysis.huge_bounds:
        tests.append({"name": "trap_no_huge_loop", "kind": "static_trap"})
    return tests


def propose_adversarial_tests(analysis: Analysis, plan: Plan | None, remaining_s: float) -> list[dict[str, Any]]:
    if remaining_s < 70 or plan is None:
        return []
    tier = choose_tier("tests", analysis.difficulty, remaining_s)
    reply = chat(
        [
            {
                "role": "system",
                "content": (
                    "Invent 2 or 3 tiny unit tests from the problem statement. "
                    "Each test must include a concrete expected value you can "
                    "derive by hand from the wording. JSON only: "
                    '{"tests":[{"name":"...","args":[...],"stdin":null,"expected":...}]}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"entrypoint={analysis.proto_entrypoint} io={analysis.io_mode}\n"
                    f"traps={plan.traps_to_handle}\nideas={plan.test_ideas}\n\n"
                    f"{analysis.statement[:2800]}"
                ),
            },
        ],
        tier=tier,
        max_tokens=config.TESTGEN_MAX_TOKENS,
        json_mode=True,
        fallback_text="",
    )
    data = extract_json(reply.text)
    out = []
    for i, raw in enumerate(data.get("tests") or []):
        if not isinstance(raw, dict) or "expected" not in raw:
            continue
        item = {
            "name": str(raw.get("name") or f"adv_{i}"),
            "kind": "adversarial",
            "expected": raw.get("expected"),
        }
        if analysis.io_mode == "stdin":
            item["stdin"] = str(raw.get("stdin") or "")
        else:
            item["args"] = raw.get("args") or []
        out.append(item)
    return out[:3]


def _static_trap_ok(code: str) -> tuple[bool, str]:
    banned = [
        "range(10**18)",
        "range(10 ** 18)",
        "range(10**9)",
        "range(10 ** 9)",
        "range(max_retries)",
        "range(spin_limit)",
        "range(credit_cap)",
    ]
    hits = [item for item in banned if item in code]
    if hits:
        return False, "code iterates a huge bound: " + ", ".join(hits)
    return True, "no obvious huge-range loop"


def _runner_script(code_path: str, entrypoint: str, io_mode: str, case: dict[str, Any]) -> str:
    payload = {
        "code_path": code_path,
        "entrypoint": entrypoint,
        "io_mode": io_mode,
        "case": case,
    }
    return f"""
import importlib.util, json
spec = json.loads({json.dumps(json.dumps(payload))})
mod_spec = importlib.util.spec_from_file_location("candidate", spec["code_path"])
mod = importlib.util.module_from_spec(mod_spec)
mod_spec.loader.exec_module(mod)
fn = getattr(mod, spec["entrypoint"], None)
if fn is None:
    raise SystemExit("missing entrypoint " + spec["entrypoint"])
case = spec["case"]
if spec["io_mode"] == "stdin":
    result = fn(case.get("stdin", ""))
else:
    result = fn(*case.get("args", []))

def default(o):
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(type(o).__name__)
print(json.dumps({{"ok": True, "result": result, "result_type": type(result).__name__}}, default=default))
"""


def _tokens(text: str) -> list[str]:
    return (text or "").split()


def _run_rust(code: str, case: dict[str, Any]) -> dict[str, Any]:
    rustc = shutil.which("rustc")
    if not rustc:
        return {"name": case["name"], "ok": True, "detail": "rustc missing; static only", "skipped": True}
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "sol.rs")
        bin_path = os.path.join(tmp, "sol")
        with open(src, "w", encoding="utf-8") as handle:
            handle.write(code)
        compiled = subprocess.run(
            [rustc, "--edition", "2021", src, "-o", bin_path],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if compiled.returncode != 0:
            err = (compiled.stderr or compiled.stdout or "compile failed").strip().splitlines()
            return {"name": case["name"], "ok": False, "detail": err[-1] if err else "compile failed"}
        try:
            proc = subprocess.run(
                [bin_path],
                input=case.get("stdin", ""),
                capture_output=True,
                text=True,
                timeout=config.VERIFY_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return {"name": case["name"], "ok": False, "detail": "timeout"}
        if proc.returncode != 0:
            return {"name": case["name"], "ok": False, "detail": (proc.stderr or "runtime error")[-200:]}
        if "expected" in case:
            if _tokens(proc.stdout) != _tokens(str(case["expected"]) if not isinstance(case["expected"], list) else " ".join(map(str, case["expected"]))):
                # if expected is a string of tokens or a list of tokens
                exp = case["expected"]
                want = _tokens(exp) if isinstance(exp, str) else [str(x) for x in exp]
                got = _tokens(proc.stdout)
                if want != got:
                    return {"name": case["name"], "ok": False, "detail": f"expected {want!r} got {got!r}"}
        return {"name": case["name"], "ok": True, "detail": "passed"}


def rust_static_check(code: str) -> VerifyReport:
    stub, reason = looks_like_stub(code, "rust")
    if stub:
        return VerifyReport(
            ok=False,
            summary=reason,
            syntax_ok="fn main" in code,
            entrypoint_ok="fn main" in code,
            stub=True,
            hints=[reason, "Implement the real stdin/stdout logic in fn main."],
        )
    if "fn main" not in code:
        return VerifyReport(
            ok=False,
            summary="missing fn main",
            hints=["Submit one complete Rust program with fn main()."],
        )
    return VerifyReport(ok=True, summary="rust static ok", syntax_ok=True, entrypoint_ok=True)


def _run_one(code: str, analysis: Analysis, case: dict[str, Any]) -> dict[str, Any]:
    if case.get("kind") == "static_trap":
        ok, detail = _static_trap_ok(code)
        return {"name": case["name"], "ok": ok, "detail": detail}
    if case.get("language") == "rust":
        return _run_rust(code, case)

    entry = analysis.proto_entrypoint or analysis.entrypoint
    io_mode = "stdin" if analysis.io_mode == "stdin" else "function"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sol.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(code)
        script = _runner_script(path, entry, io_mode, case)
        try:
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=config.VERIFY_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return {"name": case["name"], "ok": False, "detail": f"timeout after {config.VERIFY_TIMEOUT_S}s"}
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "crash").strip().splitlines()
            return {"name": case["name"], "ok": False, "detail": err[-1] if err else "crash"}
        try:
            payload = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            return {"name": case["name"], "ok": False, "detail": "bad runner output"}
        if "expected" in case and case["expected"] is not None:
            if _norm(payload.get("result")) != _norm(case["expected"]):
                return {
                    "name": case["name"],
                    "ok": False,
                    "detail": f"expected {case['expected']!r} got {payload.get('result')!r}",
                }
        expect_type = case.get("expect_type")
        if expect_type and payload.get("result_type") != expect_type:
            return {
                "name": case["name"],
                "ok": False,
                "detail": f"expected type {expect_type}, got {payload.get('result_type')}",
            }
        return {"name": case["name"], "ok": True, "detail": "passed"}


def run_tests(code: str, analysis: Analysis, tests: list[dict[str, Any]]) -> VerifyReport:
    syntax_ok, syntax_detail = syntax_check(code)
    if not syntax_ok:
        return VerifyReport(
            ok=False,
            summary=syntax_detail,
            syntax_ok=False,
            hints=["Fix the syntax error before changing logic."],
        )

    needed = analysis.proto_entrypoint or analysis.entrypoint
    if f"def {needed}" not in code:
        return VerifyReport(
            ok=False,
            summary=f"missing def {needed}",
            syntax_ok=True,
            entrypoint_ok=False,
            hints=[f"Define exactly `def {needed}(...)`."],
        )

    stub, reason = looks_like_stub(code, "python")
    if stub:
        return VerifyReport(
            ok=False,
            summary=reason,
            syntax_ok=True,
            entrypoint_ok=True,
            stub=True,
            hints=[reason, "Replace the stub with a real implementation of the stated rules."],
        )

    cases = []
    hints = []
    all_ok = True
    for case in tests:
        result = _run_one(code, analysis, case)
        result["kind"] = case.get("kind")
        cases.append(result)
        if not result["ok"]:
            hints.append(f"{result['name']}: {result['detail']}")

    passed = sum(1 for case in cases if case["ok"])
    hard_fail = [
        case
        for case in cases
        if not case["ok"] and case.get("kind") != "adversarial"
    ]
    all_ok = not hard_fail
    summary = f"{passed}/{len(cases)} local tests passed" if cases else "syntax+entrypoint ok"
    if hard_fail:
        summary += f" ({len(hard_fail)} hard failures)"
    elif any(not case["ok"] for case in cases):
        summary += " (adversarial mismatches only)"
    if not cases:
        all_ok = True
    return VerifyReport(
        ok=all_ok,
        summary=summary,
        syntax_ok=True,
        entrypoint_ok=True,
        cases=cases,
        hints=hints,
    )


def verify(code: str, analysis: Analysis, tests: list[dict[str, Any]]) -> VerifyReport:
    return run_tests(code, analysis, tests)


def benchmark(code: str, analysis: Analysis, tests: list[dict[str, Any]]) -> dict[str, Any]:
    import time

    timed = [case for case in tests if case.get("kind") in {"spec", "adversarial", "smoke"}][:2]
    if not timed:
        return {"used": False, "reason": "no runnable case"}
    start = time.monotonic()
    report = run_tests(code, analysis, timed)
    elapsed_ms = round((time.monotonic() - start) * 1000.0, 2)
    slow = elapsed_ms > config.BENCHMARK_TIMEOUT_S * 1000
    return {
        "used": True,
        "elapsed_ms": elapsed_ms,
        "ok": report.ok and not slow,
        "slow": slow,
        "summary": f"{elapsed_ms}ms over {len(timed)} cases",
    }
