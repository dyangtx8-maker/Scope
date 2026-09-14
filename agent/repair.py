"""Repair a failed solution using verifier feedback."""

from __future__ import annotations

from . import config
from .analyzer import Analysis
from .models import chat, choose_tier, extract_code
from .planner import Plan
from .verifier import VerifyReport


def repair_solution(
    analysis: Analysis,
    plan: Plan,
    code: str,
    report: VerifyReport,
    remaining_s: float,
    attempt: int = 0,
    language: str = "python",
    session_id: str = "",
) -> tuple[str, dict]:
    if remaining_s < config.MIN_REPAIR_S:
        return code, {"skipped": True, "reason": "deadline too close"}

    feedback = "\n".join(report.hints or [report.summary])
    lang = "rust" if language == "rust" else "python"
    if lang == "rust":
        system = (
            "You repair a complete Rust stdin/stdout program. "
            "Keep fn main(). Standard library only, no unsafe. "
            "Do not emit an echo stub. Return one rust markdown block."
        )
        fence = "rust"
        needed = "fn main"
    else:
        system = (
            "You repair Python 3.9 stdlib solutions. "
            f"Keep `def {analysis.proto_entrypoint or analysis.entrypoint}`. "
            "No I/O. No huge loops. No placeholders. "
            "Return one python markdown code block only."
        )
        fence = "python"
        needed = f"def {analysis.proto_entrypoint or analysis.entrypoint}"

    tier = choose_tier("repair", analysis.difficulty, remaining_s, repair_attempt=attempt)
    reply = chat(
        [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"PLAN: {plan.approach}\n"
                    f"FAILURES:\n{feedback}\n\n"
                    f"CURRENT CODE:\n```{fence}\n{code}\n```\n"
                ),
            },
        ],
        tier=tier,
        remaining_s=remaining_s,
        resume_session=session_id,
        resumable=True,
        fallback_text=code,
    )
    repaired = extract_code(reply.text, lang) or code
    if needed not in repaired:
        repaired = code
    return repaired, {
        "model": reply.model,
        "fallback": reply.fallback,
        "tier": reply.tier or tier,
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": reply.completion_tokens,
        "cost_usd": reply.cost_usd,
        "provider": reply.provider,
        "session_id": reply.session_id,
        "previous_summary": report.summary,
    }
