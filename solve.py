#!/usr/bin/env python3
"""Run the Phase 1 coding agent on one problem or all fixtures."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import config, models
from agent.orchestrator import solve_problem


def _problem_files() -> list[Path]:
    directory = config.PROBLEMS_DIR
    if not directory.exists():
        directory = config.SAMPLES_DIR
    return sorted(directory.glob("*.json"))


def _routing() -> str:
    return (
        f"runtime={config.RUNTIME} bin={config.CLAUDE_BIN}\n"
        f"  cheap  (plan, tests)      {config.CHEAP_MODEL}\n"
        f"  normal (generate, repair) {config.NORMAL_MODEL}\n"
        f"  strong (hard, escalate)   {config.STRONG_MODEL}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ChallengeBox Phase 4 agent: analyze → plan → generate → verify → repair → rust, "
        "with every model call served by the Claude CLI"
    )
    parser.add_argument("problem", nargs="?", help="Path to a problem JSON file")
    parser.add_argument("--all", action="store_true", help="Run every fixture in problems/")
    parser.add_argument("--list", action="store_true", help="List fixture paths")
    parser.add_argument("--models", action="store_true", help="Show the model routing table")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Probe the Claude CLI once to confirm it is installed and authenticated",
    )
    parser.add_argument(
        "--out",
        default=str(config.SOLUTIONS_DIR),
        help="Directory for .py and .meta.json files",
    )
    args = parser.parse_args(argv)

    if args.models:
        print(_routing())
        return 0

    if args.check:
        print(_routing())
        ok, detail = models.probe()
        print(f"claude cli: {'ok' if ok else 'FAILED'} - {detail}")
        return 0 if ok else 1

    if args.list:
        for path in _problem_files():
            print(path)
        return 0

    targets: list[Path]
    if args.all:
        targets = _problem_files()
    elif args.problem:
        targets = [Path(args.problem)]
    else:
        parser.print_help()
        return 2

    if not models.cli_available():
        print(
            f"warning: {config.CLAUDE_BIN} is not on PATH; every stage will fall back to "
            "local heuristics. Install Claude Code and run `claude login` first.",
            flush=True,
        )

    failures = 0
    for path in targets:
        print(f"\n=== {path} ===", flush=True)
        try:
            meta = solve_problem(str(path), out_dir=Path(args.out))
            print(
                f"done verified={meta['verified']} elapsed={meta['elapsed_s']}s "
                f"-> {meta['solution']}",
                flush=True,
            )
            if not meta["verified"]:
                failures += 1
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAILED {path}: {exc}", flush=True)
    return 0 if targets else 2


if __name__ == "__main__":
    raise SystemExit(main())
