"""Deadline-aware pipeline: LangGraph owns the control flow."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import config
from .graph import compiled_graph
from .models import reset_usage
from .tools import ToolContext


@dataclass
class StageEvent:
    stage: str
    elapsed_s: float
    remaining_s: float
    detail: str
    extra: dict[str, Any]


class Deadline:
    def __init__(self, total_s: float) -> None:
        self.total_s = max(1.0, float(total_s))
        self.start = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.start

    def remaining(self) -> float:
        return self.total_s - self.elapsed()

    def late(self) -> bool:
        return self.remaining() < config.LATE_PHASE_S

    def can_repair(self) -> bool:
        return self.remaining() >= config.MIN_REPAIR_S


class Orchestrator:
    def __init__(self) -> None:
        self.events: list[StageEvent] = []

    def solve_file(self, problem_path: str, out_dir: Optional[Path] = None) -> dict[str, Any]:
        reset_usage()
        path = Path(problem_path)
        raw = json.loads(path.read_text())
        deadline = Deadline(float(raw.get("deadline_s") or 300.0))
        ctx = ToolContext(problem_path=str(path), remaining_s=deadline.remaining())
        state = {
            "problem_path": str(path),
            "out_dir": str(out_dir or config.SOLUTIONS_DIR),
            "deadline": deadline,
            "events": [],
            "ctx": ctx,
            "repairs": [],
            "repair_attempt": 0,
            "rust_meta": {},
            "gen_meta": {},
            "session_id": "",
        }
        final = compiled_graph().invoke(state)
        self.events = [
            StageEvent(
                stage=ev.get("stage", ""),
                elapsed_s=float(ev.get("elapsed_s") or 0),
                remaining_s=float(ev.get("remaining_s") or 0),
                detail=ev.get("detail", ""),
                extra=dict(ev.get("extra") or {}),
            )
            for ev in (final.get("events") or [])
        ]
        return final["meta"]


def solve_problem(problem_path: str, out_dir: Optional[Path] = None) -> dict[str, Any]:
    return Orchestrator().solve_file(problem_path, out_dir=out_dir)
