"""Deadline-aware pipeline: LangGraph owns the control flow."""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import config
from .generator import python_skeleton
from .graph import compiled_graph
from .models import USAGE, log_line, reset_usage
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
        target = Path(out_dir or config.SOLUTIONS_DIR)
        log_line("")
        log_line("#" * 78)
        log_line(f"# RUN {path.name}")
        log_line("#" * 78)
        state: dict[str, Any] = {}
        try:
            # JSON is UTF-8 by spec; utf-8-sig also tolerates an editor's BOM.
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            deadline = Deadline(float(raw.get("deadline_s") or 300.0))
            ctx = ToolContext(problem_path=str(path), remaining_s=deadline.remaining())
            state = {
                "problem_path": str(path),
                "out_dir": str(target),
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
        except Exception as exc:  # noqa: BLE001
            # A missing file scores zero, so salvage whatever the run reached
            # instead of letting the exception escape with nothing written.
            return self._salvage(path, target, state, exc)
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

    def _salvage(
        self,
        path: Path,
        out_dir: Path,
        state: dict[str, Any],
        exc: BaseException,
    ) -> dict[str, Any]:
        """Write the best candidate we have after an unexpected failure."""
        ctx = state.get("ctx")
        analysis = getattr(ctx, "analysis", None)
        deadline = state.get("deadline")
        events = list(state.get("events") or [])
        detail = f"{type(exc).__name__}: {exc}"
        print(f"  ! run failed after {len(events)} events: {detail}", flush=True)

        out_dir.mkdir(parents=True, exist_ok=True)
        stem = path.stem
        code = (getattr(ctx, "code", "") or "").strip()
        if not code and analysis is not None:
            code = python_skeleton(analysis)

        solution = None
        if code:
            solution = out_dir / f"{stem}.py"
            header = (
                f"# problem_id: {getattr(analysis, 'problem_id', 'unknown')}\n"
                f"# phase: {config.PHASE}\n"
                f"# salvaged after: {detail}\n"
                f"# verified: False\n\n"
            )
            solution.write_text(header + code.rstrip() + "\n", encoding="utf-8")

        meta = {
            "phase": config.PHASE,
            "runtime": config.RUNTIME,
            "problem_id": getattr(analysis, "problem_id", "unknown"),
            "source": str(path),
            "solution": str(solution) if solution else None,
            "verified": False,
            "python_verified": False,
            "rust_verified": None,
            "salvaged": True,
            "error": detail,
            "traceback": traceback.format_exc(),
            "elapsed_s": round(deadline.elapsed(), 3) if deadline else None,
            "deadline_s": deadline.total_s if deadline else None,
            "cost_usd": round(USAGE.cost_usd, 6),
            "usage": USAGE.to_dict(),
            "events": events,
            "graph": "langgraph",
        }
        (out_dir / f"{stem}.meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        self.events = []
        return meta


def solve_problem(problem_path: str, out_dir: Optional[Path] = None) -> dict[str, Any]:
    return Orchestrator().solve_file(problem_path, out_dir=out_dir)
