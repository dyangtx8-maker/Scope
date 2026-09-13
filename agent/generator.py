"""Generate Python (and Rust) solutions from the analysis and plan."""

from __future__ import annotations

import time

from . import config
from .analyzer import Analysis
from .models import chat, choose_tier, extract_code
from .planner import Plan


def python_skeleton(analysis: Analysis) -> str:
    sig = analysis.proto_signature or analysis.signature
    name = analysis.proto_entrypoint or analysis.entrypoint
    if analysis.io_mode == "stdin":
        return (
            "def solve(stdin: str) -> str:\n"
            '    """Python prototype for a Rust stdin/stdout problem."""\n'
            "    raise NotImplementedError('prototype not generated')\n"
        )
    if "(" not in sig:
        sig = f"{name}(*args, **kwargs)"
    return (
        f"def {sig}:\n"
        f'    """Phase 2 implementation for {name}."""\n'
        "    raise NotImplementedError('generator fallback')\n"
    )


def rust_skeleton() -> str:
    return (
        "use std::io::{self, Read};\n\n"
        "fn main() {\n"
        "    let mut input = String::new();\n"
        "    io::stdin().read_to_string(&mut input).unwrap();\n"
        "}\n"
    )


def generate_python(
    analysis: Analysis,
    plan: Plan,
    remaining_s: float,
    session_id: str = "",
) -> tuple[str, dict]:
    fallback = python_skeleton(analysis)
    if remaining_s < 20:
        return fallback, {"model": "local-skeleton", "fallback": True, "tier": "none"}

    system = (
        "You write Python 3.9 standard-library solutions. "
        "No I/O, no network, no third-party imports, no global mutable state. "
        "Do not mutate caller-owned arguments. "
        "Never loop 10**9 or 10**18 times and never allocate a grid that large. "
        "Do not write placeholders, echo stubs, or NotImplementedError. "
        "Implement the real rules, even if only a correct small-input version. "
        "Return only one markdown python code block. "
        f"The required entrypoint is `{analysis.proto_entrypoint or analysis.entrypoint}`."
    )
    if analysis.io_mode == "stdin":
        system += (
            " Implement `def solve(stdin: str) -> str` that reads the full "
            "stdin text and returns the full stdout text. This is a prototype "
            "for a Rust program."
        )

    user = (
        f"SIGNATURE: {plan.python_signature or analysis.proto_signature}\n"
        f"RETURN: {plan.return_shape or analysis.return_hint}\n"
        f"APPROACH: {plan.approach}\n"
        f"DATA STRUCTURES: {plan.data_structures}\n"
        f"COMPLEXITY: {plan.complexity_target}\n"
        f"TRAPS: {plan.traps_to_handle}\n\n"
        f"STATEMENT:\n{analysis.statement}\n"
    )
    tier = choose_tier("generate", analysis.difficulty, remaining_s)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    started = time.monotonic()
    reply = chat(
        messages,
        tier=tier,
        remaining_s=remaining_s,
        budget_usd=config.GENERATE_BUDGET_USD,
        resume_session=session_id,
        resumable=True,
        fallback_text=fallback,
    )
    left = remaining_s - (time.monotonic() - started)
    if reply.provider == "local" and left > 40:
        # Nothing came back from the CLI at all: wait out a rate limit and try
        # the normal tier once before settling for the local skeleton. The
        # clock is re-read because the first attempt may have burned minutes.
        time.sleep(2.0)
        reply = chat(
            messages,
            tier="normal",
            remaining_s=left - 2.0,
            budget_usd=config.GENERATE_BUDGET_USD,
            resumable=True,
            fallback_text=fallback,
        )
    code = extract_code(reply.text, "python") or fallback
    needed = analysis.proto_entrypoint or analysis.entrypoint
    if f"def {needed}" not in code:
        code = fallback
    return code, {
        "model": reply.model,
        "fallback": reply.fallback,
        "tier": reply.tier or tier,
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": reply.completion_tokens,
        "cost_usd": reply.cost_usd,
        "kind": "python",
        "provider": getattr(reply, "provider", ""),
        "session_id": reply.session_id,
    }


def generate_rust(
    analysis: Analysis,
    plan: Plan,
    prototype: str,
    remaining_s: float,
    session_id: str = "",
) -> tuple[str, dict]:
    fallback = rust_skeleton()
    if remaining_s < 25:
        return fallback, {"model": "local-skeleton", "fallback": True, "tier": "none", "kind": "rust"}

    system = (
        "You write one complete Rust program with fn main(). "
        "Stdin/stdout only. Standard library only. No unsafe, no crates, "
        "no filesystem, no network, no nondeterminism. "
        "Never loop 10^18 times. Do not emit a stub that only echoes input. "
        "Return one markdown rust code block."
    )
    user = (
        f"APPROACH: {plan.approach}\n"
        f"TRAPS: {plan.traps_to_handle}\n\n"
        f"STATEMENT:\n{analysis.statement}\n\n"
        f"VERIFIED PYTHON PROTOTYPE (translate the behavior):\n```python\n{prototype[:6000]}\n```\n"
    )
    tier = choose_tier("generate", analysis.difficulty, remaining_s)
    reply = chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        tier=tier,
        remaining_s=remaining_s,
        budget_usd=config.GENERATE_BUDGET_USD,
        resume_session=session_id,
        resumable=True,
        fallback_text=fallback,
    )
    code = extract_code(reply.text, "rust") or fallback
    if "fn main" not in code:
        code = fallback
    return code, {
        "model": reply.model,
        "fallback": reply.fallback,
        "tier": reply.tier or tier,
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": reply.completion_tokens,
        "cost_usd": reply.cost_usd,
        "kind": "rust",
        "provider": getattr(reply, "provider", ""),
        "session_id": reply.session_id,
    }


def generate_solution(
    analysis: Analysis,
    plan: Plan,
    remaining_s: float,
    session_id: str = "",
) -> tuple[str, dict]:
    """Backward-compatible: Python solution or prototype."""
    return generate_python(analysis, plan, remaining_s, session_id=session_id)
