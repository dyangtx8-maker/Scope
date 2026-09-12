"""Produce a short structured plan before any code is written."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import config
from .analyzer import Analysis
from .models import chat, choose_tier, extract_json


@dataclass
class Plan:
    approach: str
    data_structures: list[str] = field(default_factory=list)
    complexity_target: str = ""
    traps_to_handle: list[str] = field(default_factory=list)
    python_signature: str = ""
    return_shape: str = ""
    test_ideas: list[str] = field(default_factory=list)
    model: str = ""
    fallback: bool = False
    emit_rust: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _heuristic_plan(analysis: Analysis) -> Plan:
    traps = (
        analysis.algorithmic_traps
        + analysis.wording_traps
        + analysis.materialization_risks
        + analysis.time_memory_risks
    )
    if analysis.target_language == "rust":
        approach = (
            "Prototype the stdin/stdout spec in Python as solve(stdin) -> str, "
            "verify tiny cases, then emit a complete Rust fn main() using only "
            "the standard library. Never loop a 1e18 bound."
        )
    else:
        approach = (
            "Write a direct Python implementation of the stated rules. "
            "Never loop or allocate using a 1e9/1e18 bound; iterate input "
            "size only. Prefer maps, difference arrays, or lazy simulation."
        )
    return Plan(
        approach=approach,
        data_structures=["dicts/lists sized by input", "avoid dense grids"],
        complexity_target="near-linear in the given input length",
        traps_to_handle=traps[:10],
        python_signature=analysis.proto_signature or analysis.signature,
        return_shape=analysis.return_hint,
        test_ideas=[
            "smallest legal input with a known expected value from the spec",
            "invalid / implicit-error / boundary wording trap",
            "a case that would explode if a huge bound is looped",
        ],
        model="local-heuristic",
        fallback=True,
        emit_rust=analysis.target_language == "rust",
    )


def plan_solution(analysis: Analysis, remaining_s: float) -> Plan:
    fallback = _heuristic_plan(analysis)
    if remaining_s < 25:
        return fallback

    prompt = {
        "target_language": analysis.target_language,
        "entrypoint": analysis.entrypoint,
        "proto_signature": analysis.proto_signature,
        "io_mode": analysis.io_mode,
        "return_hint": analysis.return_hint,
        "difficulty": analysis.difficulty,
        "huge_bounds": analysis.huge_bounds,
        "traps": {
            "materialization": analysis.materialization_risks,
            "wording": analysis.wording_traps,
            "algorithmic": analysis.algorithmic_traps,
            "time_memory": analysis.time_memory_risks,
        },
        "constraints": analysis.constraints,
        "statement": analysis.statement[:3500],
    }
    tier = choose_tier("plan", analysis.difficulty, remaining_s)
    reply = chat(
        [
            {
                "role": "system",
                "content": (
                    "You plan solutions for hard algorithmic problems. "
                    "Reply with a JSON object only. Do not write code. "
                    "Huge numeric bounds must not be materialized or looped. "
                    "If target_language is rust, plan a Python prototype first "
                    "and a later Rust translation."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Create a concise plan with keys: approach, "
                    "data_structures (array), complexity_target, "
                    "traps_to_handle (array), python_signature, "
                    "return_shape, test_ideas (array).\n\n"
                    f"{prompt}"
                ),
            },
        ],
        tier=tier,
        max_tokens=config.PLAN_MAX_TOKENS,
        json_mode=True,
        fallback_text="",
    )
    data = extract_json(reply.text)
    if not data:
        fallback.model = reply.model
        fallback.fallback = True
        return fallback

    def _list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(x) for x in value][:12]
        if value:
            return [str(value)]
        return []

    return Plan(
        approach=str(data.get("approach") or fallback.approach),
        data_structures=_list(data.get("data_structures")) or fallback.data_structures,
        complexity_target=str(data.get("complexity_target") or fallback.complexity_target),
        traps_to_handle=_list(data.get("traps_to_handle")) or fallback.traps_to_handle,
        python_signature=str(data.get("python_signature") or analysis.proto_signature),
        return_shape=str(data.get("return_shape") or analysis.return_hint),
        test_ideas=_list(data.get("test_ideas")) or fallback.test_ideas,
        model=reply.model,
        fallback=reply.fallback,
        emit_rust=analysis.target_language == "rust",
    )
