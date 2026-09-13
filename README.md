# AI Challenge Problem Solver

See [ARCHITECTURE.md](ARCHITECTURE.md) for the agent design (LangGraph, model routing, verification, deadline).

## Summary

This project tests your ability to build an AI system that solves algorithmic challenge problems.

Each problem comes as a JSON file with a statement, a target language, and an entrypoint.
Your system must read the problem, produce a working solution, and return it before the deadline.

The problems are hard on purpose.
Most of them hide traps, such as huge numeric bounds, structures that cannot be fully built in memory, or small wording details that change the answer.
A single "write the code" prompt will usually fail.

You should consider cost optimization in architecture.

## Input Format

Each problem is a JSON file like this:

```json
{
  "problem_id": "1ba0d34f...",
  "language": "python",
  "statement": "Simulate a congestion-aware ...",
  "entrypoint": "simulate_writes",
  "public_examples": [],
  "deadline_s": 300.0
}
```

| Field | Meaning |
|-------|---------|
| `problem_id` | Unique ID for the problem |
| `language` | `python` or `rust` |
| `statement` | The full problem description |
| `entrypoint` | The function name for Python, or `main` for Rust |
| `public_examples` | Sample test cases (may be empty) |
| `deadline_s` | Time limit in seconds for producing a solution |

Solution requirements by language:

* **Python:** Define the function named in `entrypoint`. Use only the standard library. No I/O.
* **Rust:** Write one complete program with `fn main()`. Read from stdin and write to stdout. Use only the standard library.

Sample problems are included in the `problems/` folder of this repo.

## Rules

Each problem is scored as 0 or 1.

* **1 point:** The solution is returned within the deadline and passes all hidden test cases.
* **0 points:** The solution is late, fails to run, or gets any hidden test case wrong.

Hidden test cases include edge cases and maximum-size inputs, so the solution must be both correct and fast.

## What We Want From You

Build a system that uses AI to solve these problems reliably and avoids their traps.

We accept either of the following:

1. **Running code (preferred):** A working tool that takes a problem JSON file and outputs a solution file within the deadline.
2. **AI architecture:** A clear design document explaining how your system works, how it checks correctness without public examples, and how it stays within the time limit.

Whichever you choose, please explain:

* How your system finds and handles the traps in each problem.
* How it verifies a solution before submitting it.
* What it does when it is running out of time.

## Phase 2 solver

Phase 2 keeps the same pipeline and adds correctness pressure:

* spec-derived expected tests and adversarial test generation
* stub / echo-solution rejection
* cheap → normal → strong model routing
* a real local benchmark when tests pass
* Rust emission for Rust samples: verify a Python prototype, then write `fn main()`

```text
problem JSON
  → analyzer (local traps / bounds)
  → planner (cheap model)
  → Python generator (stronger model on hard problems)
  → spec + adversarial verifier
  → repair loop (escalates model after a miss)
  → optional Rust translation
  → solutions/<name>.py or .rs + .meta.json
```

```bash
python3 solve.py --list
python3 solve.py problems/problem_01.json
python3 solve.py --all
python3 tests/test_architecture.py
```

No provider API key lives in this project: every model call is made by the Claude CLI, which brings its own authentication. Review token use, cost, routing, and repairs in `solutions/*.meta.json`.

## Phase 3 graph

Control flow is a small LangGraph in `agent/graph.py`. Stages are unchanged.

## Phase 4 runtime: Claude models through the Claude CLI

Every tier is now a Claude model, and the only transport is the Claude Code
CLI (`claude -p`). There is no provider SDK, no HTTP client, and no API key in
this repo.

| Job | Tier | Model |
|---|---|---|
| Plan, adversarial test ideas | cheap | `claude-haiku-4-5-20251001` |
| Python / Rust generation, repair | normal | `claude-sonnet-5` |
| Hard problems, escalated repair | strong | `claude-opus-5` |
| Trap extraction, spec tests, stub check, benchmark, disk write | — | local, no model |

The CLI, not this code, owns authentication, overload fallback
(`--fallback-model`), prompt caching and session storage. `agent/models.py`
only builds the argv, writes the prompt to stdin, and reads back the single
JSON result object.

### Setup

```bash
npm install -g @anthropic-ai/claude-code   # provides the `claude` binary
claude login                               # or export ANTHROPIC_API_KEY
pip3 install -r requirements.txt
python3 solve.py --check                   # one tiny call proves auth works
python3 tests/test_architecture.py         # no network, no CLI needed
python3 solve.py problems/problem_01.json
```

`--all --jobs N` solves N problems at once in a process pool; each keeps its
own deadline, so 4 is a sane ceiling on a laptop. Generation and test-writing
already overlap within a single problem (`CLAUDE_PARALLEL_STAGES=0` disables
it), which measured 259s → 182s on `problem_01`.

Exit codes: `0` every problem verified, `1` a solution was written but failed
local verification, `2` bad usage, `3` a run crashed or had to be salvaged.

`solve.py --models` prints the routing table. Every model, timeout and spend
cap is overridable from the environment or `.env` — see `.env.example`.

On Windows `npm` installs a `claude.cmd` shim, which `CreateProcess` will only
run under its exact filename, so the agent resolves the binary to a full path
before launching it. If `--check` still reports that it cannot execute the CLI,
point `CLAUDE_BIN` at the file directly:

```powershell
$env:CLAUDE_BIN = "$env:APPDATA\npm\claude.cmd"
python solve.py --check
```

> `solutions/*.meta.json` checked into this repo are the records of an older
> run on the previous providers. Re-run the solver to regenerate them with
> Claude models.