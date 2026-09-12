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

Uses `GROQ_API_KEY`, `GEMINI_API_KEY`, and optional `OPENAI_API_KEY` from `.env`. Review token use, routing, and repairs in `solutions/*.meta.json`.

## Phase 3 graph

Control flow is a small LangGraph in `agent/graph.py`. Stages are unchanged.

* Plan and adversarial tests: Gemini `gemini-2.5-flash-lite`
* Generate and repair: Groq `openai/gpt-oss-120b`
* Groq failure: OpenAI `gpt-4.1-mini`
* Analyzer, verifier, benchmark: local

```bash
pip3 install -r requirements.txt
python3 tests/test_architecture.py
python3 solve.py problems/problem_01.json
```