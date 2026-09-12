"""Cheap local problem analyzer.

This stage does not call a model. It extracts the interface, numeric
bounds, and likely traps so later stages can stay focused.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


_SIGNATURE = re.compile(
    r"(?:Define|Implement)\s+`([a-zA-Z_]\w*\([^`]*)`",
    re.I,
)
_HUGE = re.compile(
    r"10\s*\*\*\s*\d+|10\^\d+|10\*\*\d+|\b\d{1,3}(?:,\d{3}){1,3}\b|\b10\s*\*\*\s*18\b",
)
@dataclass
class Analysis:
    problem_id: str
    source_language: str
    target_language: str
    entrypoint: str
    signature: str
    io_mode: str
    statement: str
    deadline_s: float
    public_examples: list[Any] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    huge_bounds: list[str] = field(default_factory=list)
    materialization_risks: list[str] = field(default_factory=list)
    wording_traps: list[str] = field(default_factory=list)
    algorithmic_traps: list[str] = field(default_factory=list)
    time_memory_risks: list[str] = field(default_factory=list)
    return_hint: str = ""
    difficulty: str = "normal"
    proto_entrypoint: str = ""
    proto_signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Keep prompts smaller; the full statement is already on disk.
        data["statement"] = self.statement
        return data

    def compact(self) -> dict[str, Any]:
        data = self.to_dict()
        data["statement"] = self.statement[:1200]
        return data


def _interface(problem: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    """Return target_language, entrypoint, signature, io_mode, proto_entry, proto_sig."""
    source = (problem.get("language") or "python").lower()
    raw_entry = problem.get("entrypoint") or "solve"
    statement = problem.get("statement") or ""
    match = _SIGNATURE.search(statement)
    if source == "rust" or raw_entry == "main":
        return (
            "rust",
            "main",
            "fn main()",
            "stdin",
            "solve",
            "solve(stdin: str) -> str",
        )
    if match:
        sig = match.group(1)
        if not sig.endswith(")"):
            sig = sig + ")"
        name = sig.split("(", 1)[0]
        return "python", name, sig, "function", name, sig
    return "python", raw_entry, f"{raw_entry}(*args)", "function", raw_entry, f"{raw_entry}(*args)"


def _return_hint(statement: str) -> str:
    patterns = [
        (r"Return a dictionary.*", "dict"),
        (r"Return the resulting list", "list"),
        (r"Return a Python `list\[int\]`", "list[int]"),
        (r"Return the 1-based index", "int"),
        (r"Return the final protected union", "list"),
        (r"print", "stdout tokens"),
    ]
    for pattern, hint in patterns:
        if re.search(pattern, statement, re.I):
            return hint
    return "see statement"


def _collect(statement: str, needles: list[str], note: str) -> list[str]:
    hits = []
    lower = statement.lower()
    for needle in needles:
        if needle in lower:
            hits.append(f"{note}: {needle}")
    return hits


def analyze_problem(problem: dict[str, Any]) -> Analysis:
    statement = problem.get("statement") or ""
    target_language, entrypoint, signature, io_mode, proto_entry, proto_sig = _interface(problem)
    huge = sorted(set(_HUGE.findall(statement)))

    constraints = []
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("constraints") or "at most" in stripped.lower():
            constraints.append(stripped[:240])

    materialization = _collect(
        statement,
        [
            "do not enumerate",
            "must not enumerate",
            "cannot be fully built",
            "enormous",
            "unfold",
            "flatten",
            "materializ",
            "independently through all edits",
        ],
        "materialization",
    )
    wording = _collect(
        statement,
        [
            "implicit",
            "exactly",
            "without going below",
            "simultaneously",
            "attached to the same identifier",
            "never processed",
            "location-authoritative",
            "remembered paths are never resolved again",
            "one-based",
            "zero-based",
        ],
        "wording",
    )
    algorithmic = _collect(
        statement,
        [
            "run-length",
            "persistent",
            "branch",
            "overlay",
            "reverse replacement order",
            "grapheme",
            "utf-16",
            "capture",
            "alpha-renaming",
            "schema",
            "fan-in",
        ],
        "algorithm",
    )
    time_memory = []
    if huge:
        time_memory.append("numeric bounds too large for naive loops or arrays")
    if re.search(r"200,?000|300,?000|500,?000|150,?000", statement):
        time_memory.append("n up to ~1e5–5e5; need near-linear work")
    if "10**9" in statement or "10^9" in statement or "10**18" in statement:
        time_memory.append("cannot allocate by coordinate or iterate a bound")

    difficulty = "normal"
    if len(huge) >= 2 or materialization or "10**18" in statement or "10^18" in statement:
        difficulty = "hard"
    if (problem.get("language") or "").lower() == "rust":
        difficulty = "hard"

    return Analysis(
        problem_id=str(problem.get("problem_id") or "unknown"),
        source_language=str(problem.get("language") or "python"),
        target_language=target_language,
        entrypoint=entrypoint,
        signature=signature,
        io_mode=io_mode,
        statement=statement,
        deadline_s=float(problem.get("deadline_s") or 300.0),
        public_examples=list(problem.get("public_examples") or []),
        constraints=constraints[:8],
        huge_bounds=huge,
        materialization_risks=materialization,
        wording_traps=wording,
        algorithmic_traps=algorithmic,
        time_memory_risks=time_memory,
        return_hint=_return_hint(statement),
        difficulty=difficulty,
        proto_entrypoint=proto_entry,
        proto_signature=proto_sig,
    )
