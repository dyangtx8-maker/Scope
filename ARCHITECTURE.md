# ChallengeBox agent architecture

This repo is an AI coding agent for trap-heavy algorithmic problems. The grader scores hidden tests we never see. Sample JSON files ship **no public examples**. The agent must read a spec, find traps, generate stdlib code, verify locally, repair if needed, and write a solution before `deadline_s`.

The control loop is a small **LangGraph** state machine. The brains of each step stay in ordinary Python modules. There is no CrewAI and no extra agent swarm.

![ChallengeBox agent architecture](docs/challengebox-architecture.png)

## Pipeline

```text
problem JSON
  → analyze (local)
  → plan (Claude Haiku 4.5)
  ├─ generate (Claude Sonnet 5, Opus 5 on hard problems) ─┐   in parallel
  └─ tests (local spec + optional Claude Haiku adversarial)┘
  → verify (local)
       ├─ fail + time left → repair (Claude, escalating tier) → verify
       ├─ Rust target → emit fn main() (Claude)
       └─ write solutions/<name>.py or .rs + .meta.json
```

`python3 solve.py <problem.json>` is the only entrypoint. It calls `Orchestrator.solve_file`, which builds deadline state and `invoke`s the compiled graph in `agent/graph.py`.

## Why LangGraph is here

LangGraph owns **edges**, not prompts:

- verify → repair when tests fail and the deadline still allows another attempt
- verify → emit_rust when the source language is Rust
- otherwise → write the best candidate so far

Keep-best repair (reject a worse rewrite) lives inside the repair node. Analyzer, planner, generator, and verifier are unchanged node bodies.

## Model routing

Every tier is a Claude model and the only transport is the Claude Code CLI.

| Job | Tier | Model | `--effort` |
|---|---|---|---|
| Plan, adversarial test ideas | cheap | `claude-haiku-4-5-20251001` | low |
| Python / Rust generation, repair | normal | `claude-sonnet-5` | medium |
| Hard problems, escalated repair | strong | `claude-opus-5` | high |
| Trap extraction, spec tests, stub check, benchmark, disk write | — | local, no model | — |

Codegen never runs on the cheap tier. On failure `agent/models.py` walks the chain requested model → `claude-sonnet-5` → `claude-haiku-4-5` → local fallback text, and hands the rest of the chain to `claude --fallback-model` so plain overload is retried inside a single invocation.

No key lives in this repo. The CLI authenticates itself (`claude login`, or `ANTHROPIC_API_KEY` / Bedrock / Vertex in the environment or `.env`); `.env` values starting with `ANTHROPIC_`, `AWS_` or `CLAUDE_CODE_USE_` are passed through to the child process and never logged.

## The Claude CLI transport

`agent/models.py` builds one argv, writes the prompt to the child's stdin and reads back the single JSON result object from `--output-format json`. The calls are hardened for unattended use:

| Flag | Why |
|---|---|
| `--tools ""` | these calls are pure text generation; the child gets no filesystem, shell or network tools |
| `--permission-prompts none` | nothing can block waiting for a human |
| `--safe-mode`, `--strict-mcp-config` | a developer's CLAUDE.md, skills, hooks, plugins or MCP servers cannot change what the agent generates |
| private empty `cwd` | no repo file or project memory leaks into the prompt |
| explicit `--session-id` | a parent Claude Code process exports `CLAUDE_CODE_SESSION_ID`; an inherited id must never become resumable |
| `--json-schema` | plan and adversarial-test JSON is validated by the CLI and returned as `structured_output` |
| `--max-budget-usd` | per-stage spend guardrail, the CLI-era replacement for `max_tokens` |
| `--effort` | the replacement for the old provider `reasoning_effort`, stepped down one notch when under `EFFORT_DOWNGRADE_S` |
| `--fallback-model` | overload retries happen inside one invocation |

`stdout` is parsed line by line: the CLI can print a diagnostic line (for example `[claude-code:unrecognized_model] …`) before the result object, and a failure can arrive as `is_error: true` with `subtype: "success"`, so both are checked explicitly.

**Session reuse.** Generation runs in a session this process names. Repair and Rust emission resume it with `--resume … --fork-session`, so the model still has the statement and its own earlier code in context, the prompt cache stays warm, and a rejected repair cannot corrupt the branch it came from. Only session ids minted in this process may be resumed. If a resume fails the call is retried once as a fresh conversation, and the repair prompt is self-contained either way.

**Structured output beats prompted JSON.** Asking for "a JSON object only" in the
prompt is not reliable through the CLI: on a same-prompt comparison the schema run
returned a usable plan for $0.068 while the prompt-only run returned text this repo
could not parse, for $0.098, in the same wall clock. Both JSON stages therefore ship
a schema, and `chat()` still falls back to prompted JSON if the CLI ever rejects one.

**Timeouts.** Every call is bounded by `min(CLI_TIMEOUT_S, remaining - RESERVE_S)`. Under `CLI_MIN_CALL_S` the stage does not call a model at all and takes its local fallback, so the writer always keeps its reserve. A CLI turn costs process start-up plus thinking time, so `LATE_PHASE_S` (70s), `MIN_REPAIR_S` (60s) and `ADVERSARIAL_MIN_S` (90s) were raised from their HTTP-era values: each one now has to cover a whole round trip, not a fast API call.

## Parallelism

Two independent axes, both off by default only where they cost something.

**Stages.** `tests` reads the analysis and the plan; it never reads the
generated code. The sequential `generate → tests` edge was wiring, not a data
dependency, so `plan` now fans out to both and `verify` joins them. Same
calls, same cost, shorter critical path. Measured on `problem_01`:

| | sequential | parallel |
|---|---|---|
| plan | 121s | 121s |
| generate / tests | 55s then 83s | overlapped, 61s |
| total | **259s** | **182s** |
| benchmark | skipped, no time left | ran |
| cost | $0.193 | $0.197 |

The 77s does not just come off the clock - it goes back into the deadline
budget, which is why the run that used to skip its benchmark now has time for
it, and for a repair round if verification had failed.

Two consequences fell out of the fan-out and are worth knowing before editing
a node. `events` is an `Annotated[list, operator.add]` reducer, so nodes
return **only the events they produced**; returning the accumulated list would
duplicate it. And no node returns `ctx` any more - it is one shared mutable
object, so two branches returning it is a concurrent write to one key, which
LangGraph rejects. `_sync_ctx` takes a lock because LangGraph runs sync nodes
on a thread pool.

Set `CLAUDE_PARALLEL_STAGES=0` to go back to the sequential edge on a machine
where two concurrent CLI processes contend.

**Problems.** `solve.py --all --jobs N` solves N problems at once in a process
pool. Each problem keeps its own independent deadline and writes to its own
files, so this is free parallelism - no extra model calls. Processes rather
than threads because `models.USAGE` is a module-global meter that
`solve_file` resets on entry; two problems sharing an interpreter would
clobber each other's cost accounting. Worker output is captured and replayed
as one block per problem, because interleaved stage lines from four problems
at once are unreadable.

The ceiling is the machine, not the code. Every `claude -p` is a full Node
process, and each problem's deadline starts when its worker picks it up, so
oversubscribing spends the deadline on contention: during development two
concurrent pipelines turned a 20s Haiku call into 114s. 4 is a sane ceiling on
a laptop.

## Measured behaviour

Two sample runs on this transport, 300s deadline each, Claude CLI 2.1.270:

| Problem | Models used | Wall clock | Cost | Local verdict |
|---|---|---|---|---|
| `problem_01`, python, hard | haiku ×2, opus ×1 | 229s | $0.20 | 8/8 local tests, `verified=true` |
| `problem_02`, rust, hard | haiku ×1, opus ×1 (timed out), sonnet ×2 | 247s | $0.12 | Rust program emitted, prototype missed one spec case |

The second run is the fallback ladder doing its job: the Opus generate attempt hit
the 120s cap, `generate_python` saw a local fallback with time still on the clock,
retried on the normal tier, and Sonnet produced the prototype and the `fn main()`.

A CLI turn costs far more wall clock than the HTTP call it replaced - 20s to 140s
here against seconds - so a 300s deadline buys roughly two or three model turns.
That is what the raised `LATE_PHASE_S`, `MIN_REPAIR_S` and `ADVERSARIAL_MIN_S`
thresholds are for: the budget goes into one good candidate instead of repair
rounds that cannot finish. Both runs still wrote a solution well inside the
deadline.

## Stages

**Analyze** (`agent/analyzer.py`). No LLM. Parses entrypoint, Python vs Rust, huge bounds (`10**18`, `200,000`, …), materialization warnings, wording traps, and a difficulty tag.

**Plan** (`agent/planner.py`). Cheap-tier Claude JSON, validated against `PLAN_SCHEMA` by `claude --json-schema`: approach, structures, complexity target, traps, test ideas. Falls back to a local heuristic plan built from the analyzer alone - same eleven fields, with the canned approach text and the analyzer's signature, return hint and trap list. `CLAUDE_PLAN_LLM=0` skips the model call entirely and takes that heuristic directly: on `problem_01` that turned a 182s run into a 65s one with the same 8/8 verdict and the same cost, because the plan call was timing out at its 120s cap anyway. Whether it helps where the plan *succeeds* is unmeasured.

**Generate** (`agent/generator.py`). Python stdlib function named by the spec. Rust samples first get a Python `solve(stdin) -> str` prototype, then a `fn main()` program.

**Tests** (`agent/verifier.py` + `GenerateTestsTool`). Spec cases with expected values derived from the statement (empty, invalid, implicit error, …). Optional adversarial cases from the cheap Claude tier. Adversarial mismatches are **soft** so a wrong LLM expected value cannot fail a spec-correct solution.

**Verify**. Syntax, required entrypoint, stub/echo rejection, subprocess run with timeout, expected-value compare. Static ban on `range(10**18)`-style loops.

**Repair**. Up to 3 attempts, resuming the generation session. Keep the higher-scoring candidate. Near the deadline (`LATE_PHASE_S` / `MIN_REPAIR_S`), skip more LLM work and write whatever is best.

**Write**. Always emits a file before the clock hits `RESERVE_S`.

## Failure containment

A missing solution file scores zero, so an unexpected exception must never be
the reason one is absent. Two layers:

* **Node guards.** `plan`, `generate`, `tests`, `verify`, `repair` and
  `emit_rust` catch anything their stage raises, log
  `node failed, falling back locally: <error>`, and substitute the local
  fallback that stage already has - the heuristic plan, the stdlib skeleton,
  the spec-only test list. The graph keeps running and `write` still executes.
* **Salvage.** `Orchestrator.solve_file` wraps the whole run, `invoke`
  included. Anything the guards cannot absorb still writes the in-flight
  candidate from the shared `ToolContext` plus a `.meta.json` carrying
  `salvaged: true`, the error, the traceback and the events so far.

`solve.py` prints the traceback for a genuine crash rather than one bare line,
and its exit code says what happened: `0` all verified, `1` written but
unverified, `2` bad usage, `3` a run that crashed or had to be salvaged.

## Encoding

All I/O is pinned to UTF-8 rather than the platform's locale codec, because on
a stock Windows box that codec is cp1252:

* the CLI subprocess pipes (`encoding="utf-8", errors="replace"`) - without
  this the node CLI's UTF-8 reply arrives as mojibake (`∈` reads as `âˆˆ`),
  a reply containing a character outside cp1252 kills the reader thread and
  the call is reported as empty output, and a prompt containing one raises
  `UnicodeEncodeError` out of `communicate`;
* problem JSON, read as `utf-8-sig` so an editor's BOM is tolerated;
* `.env`, decoded by sniffing the BOM first (a UTF-8 BOM used to be glued onto
  the first key name; PowerShell 5.1's `>` writes UTF-16LE), then UTF-8, then
  the local code page - saying on stderr when it had to guess;
* `solve.py` reconfigures stdout and stderr, so a redirected run cannot die on
  an em dash. `.meta.json` records traps, plan, usage by model/tier, the dollar cost the CLI reported, graph node names, and every timed event.

## Tools

Generic `Tool` interface in `agent/tools.py`. The graph decides `should_use`:

- `read_problem`
- `analyze_constraints`
- `generate_tests`
- `run_solution`
- `inspect_failure`
- `benchmark_solution` (skipped if tests still fail or time is short)

## Deadline

`deadline_s` from the problem JSON (300s on samples) is first-class. Logs look like:

```text
[  21.80s | rem  278.20s] generate: python chars=8619 model=claude-sonnet-5 tier=normal provider=claude-cli cost=$0.0412
```

## Repository layout

```text
solve.py                 CLI
agent/graph.py           LangGraph nodes and edges
agent/orchestrator.py    deadline + invoke
agent/analyzer.py        local trap detection
agent/planner.py         structured plan
agent/generator.py       Python / Rust codegen
agent/verifier.py        tests + stubs
agent/repair.py          failure → rewrite
agent/models.py          Claude CLI transport, routing, usage/cost meter
agent/tools.py           observable tools
agent/config.py          models, CLI flags, timeouts, spend caps, repair limits
samples/                 original challenge JSON
problems/                numbered copies of the same fixtures
solutions/               generated code + .meta.json
tests/test_architecture.py
```

## Language rules (grader)

- **Python:** define `entrypoint`, stdlib only, no I/O.
- **Rust:** complete `fn main()`, stdin/stdout, stdlib only.
- Hidden tests include max-size inputs. Local `verified=True` is **not** the hidden score. Sample JSON has `"public_examples": []`.

## Run

```bash
npm install -g @anthropic-ai/claude-code   # the `claude` binary this agent shells out to
claude login                               # or export ANTHROPIC_API_KEY
pip3 install -r requirements.txt
python3 solve.py --check                   # one tiny call proves the CLI is wired up
python3 tests/test_architecture.py         # offline: no CLI, no network
python3 solve.py samples/<id>.json
python3 solve.py --all
```

Without the `claude` binary the agent still runs end to end: every stage takes its local fallback and writes a heuristic solution, and `solve.py` says so on startup.
