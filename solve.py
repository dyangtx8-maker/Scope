#!/usr/bin/env python3
"""Run the Phase 1 coding agent on one problem or all fixtures."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import config
from agent.orchestrator import solve_problem


def _problem_files() -> list[Path]:
    directory = config.PROBLEMS_DIR
    if not directory.exists():
        directory = config.SAMPLES_DIR
    return sorted(directory.glob("*.json"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ChallengeBox Phase 2 agent: analyze → plan → generate → verify → repair → rust"
    )
    parser.add_argument("problem", nargs="?", help="Path to a problem JSON file")
    parser.add_argument("--all", action="store_true", help="Run every fixture in problems/")
    parser.add_argument("--list", action="store_true", help="List fixture paths")
    parser.add_argument(
        "--out",
        default=str(config.SOLUTIONS_DIR),
        help="Directory for .py and .meta.json files",
    )
    args = parser.parse_args(argv)

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
