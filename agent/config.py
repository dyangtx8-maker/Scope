"""Runtime knobs for the Phase 4 agent.

Every model call goes through the Claude Code CLI (`claude -p`), so there is
no provider SDK, no HTTP client, and no per-provider API key in this project.
Authentication, overload retries, and prompt caching are the CLI's job.
"""

from __future__ import annotations

import codecs
import locale
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBLEMS_DIR = ROOT / "problems"
SOLUTIONS_DIR = ROOT / "solutions"
SAMPLES_DIR = ROOT / "samples"
ENV_PATH = ROOT / ".env"

PHASE = 4
RUNTIME = "claude-cli"


def _clean_value(raw: str) -> str:
    """Quoted value, or bare value up to an unquoted trailing comment."""
    value = raw.strip()
    if value[:1] in {'"', "'"}:
        quote = value[0]
        end = value.find(quote, 1)
        if end > 0:
            return value[1:end]
        return value[1:]
    # `KEY=120   # hard cap` must not parse as "120   # hard cap".
    for index in range(len(value)):
        if value[index] == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].strip()
    return value


def decode_env(data: bytes) -> str:
    """Decode a .env file without ever raising.

    Editors on Windows happily write a UTF-8 BOM, and PowerShell 5.1's `>`
    writes UTF-16LE; both make a naive read either drop the first key or fail.
    A file an editor saved in the local code page still decodes correctly,
    and only a genuinely undecodable file loses bytes - loudly.
    """
    for bom, codec in (
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if data.startswith(bom):
            try:
                return data.decode(codec)
            except (UnicodeDecodeError, LookupError):
                break
    for codec in ("utf-8", locale.getpreferredencoding(False), "cp1252"):
        try:
            text = data.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
        if codec != "utf-8":
            print(f"config: .env is not UTF-8; read as {codec}", file=sys.stderr)
        return text
    print("config: .env is not decodable; undecodable bytes replaced", file=sys.stderr)
    return data.decode("utf-8", errors="replace")


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """Parse a tiny KEY=value .env file. Missing file means empty settings."""
    env: dict[str, str] = {}
    try:
        raw_text = decode_env(path.read_bytes())
    except OSError:
        return env
    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if key:
            env[key] = _clean_value(value)
    return env


def setting(name: str, default: str) -> str:
    """Process environment wins, then .env, then the built-in default."""
    # A real environment variable is taken literally; only the .env file has
    # comment syntax to strip.
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    value = load_env().get(name)
    return value if value else default


def _flag(name: str, default: bool) -> bool:
    raw = setting(name, "1" if default else "0").strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


def _number(name: str, default: str, cast):
    """Never let one bad line in .env stop the agent from starting."""
    raw = setting(name, default)
    try:
        return cast(raw)
    except (TypeError, ValueError):
        print(
            f"config: ignoring {name}={raw!r} (not a number), using {default}",
            file=sys.stderr,
        )
        return cast(default)


def _float(name: str, default: str) -> float:
    return _number(name, default, float)


def _int(name: str, default: str) -> int:
    return _number(name, default, int)


# ----------------------------------------------------------------- transport
CLAUDE_BIN = setting("CLAUDE_BIN", "claude")
PROVIDER = "claude-cli"

# One provider now. The tier names stay because the routing table is still
# cheap -> normal -> strong; only the models behind them changed.
CHEAP_PROVIDER = PROVIDER
NORMAL_PROVIDER = PROVIDER
STRONG_PROVIDER = PROVIDER
FALLBACK_PROVIDER = PROVIDER

# Plan / adversarial tests.
CHEAP_MODEL = setting("CLAUDE_CHEAP_MODEL", "claude-opus-5")
# Python / Rust generation and repair.
NORMAL_MODEL = setting("CLAUDE_NORMAL_MODEL", "claude-opus-5")
# Hard problems and escalated repairs.
STRONG_MODEL = setting("CLAUDE_STRONG_MODEL", "claude-opus-5")
# The emergency rung. Every stage runs Opus, but a chain of one model means a
# timeout goes straight to the local skeleton - which is how problem_04 ended
# up with a NotImplementedError stub. Keep one cheaper, faster model behind it.
FALLBACK_MODEL = setting("CLAUDE_FALLBACK_MODEL", "claude-sonnet-5")

# `claude --effort` replaces the old provider-specific reasoning_effort knob.
EFFORT_BY_TIER = {"cheap": "low", "normal": "medium", "strong": "high"}
EFFORT_LADDER = ["low", "medium", "high"]
# Below this much wall clock, drop one effort step so a slow think cannot eat
# the deadline.
EFFORT_DOWNGRADE_S = 150.0

CLI_TIMEOUT_S = _float("CLAUDE_CLI_TIMEOUT_S", "240")
CLI_MIN_CALL_S = _float("CLAUDE_CLI_MIN_CALL_S", "20")
CLI_ATTEMPTS = _int("CLAUDE_CLI_ATTEMPTS", "2")
# Fraction of the remaining budget one attempt may spend while another model is
# still available. Without it the first model consumes the whole pool on a
# timeout and the fallback never gets a usable window.
CLI_FIRST_ATTEMPT_SHARE = _float("CLAUDE_CLI_FIRST_ATTEMPT_SHARE", "0.6")
# Ignore CLAUDE.md, skills, plugins, hooks and MCP servers so a developer's
# local Claude Code setup cannot change what this agent generates.
CLI_SAFE_MODE = _flag("CLAUDE_CLI_SAFE_MODE", True)
# "" disables every built-in tool: these calls are pure text generation.
CLI_TOOLS = setting("CLAUDE_CLI_TOOLS", "")
# Working directory for the child CLI. Empty means a private temp directory.
CLI_WORKDIR = setting("CLAUDE_CLI_CWD", "")
# Resume the generate session during repair / Rust emission so the model keeps
# the statement in context and the prompt cache stays warm.
REUSE_SESSION = _flag("CLAUDE_REUSE_SESSION", True)
# Run the test-writing stage alongside generation instead of after it. They
# share no data - tests reads the analysis and the plan, never the code - so
# overlapping them hands the saved wall clock back to the repair budget. Set
# to 0 on a machine where two concurrent CLI processes contend.
PARALLEL_STAGES = _flag("CLAUDE_PARALLEL_STAGES", True)

# Ask a model for the plan at all. With 0 the planner returns its local
# heuristic immediately - no CLI call, no cost - which hands the 70-130s a
# plan call takes back to generation and repair. The analyzer still supplies
# the signature, return hint and trap list either way.
PLAN_LLM = _flag("CLAUDE_PLAN_LLM", True)
# Append the exact argv, system prompt, user prompt and reply of every CLI call
# to this path. Empty disables it. solve.py --log-prompts PATH sets it too.
PROMPT_LOG = setting("CLAUDE_PROMPT_LOG", "")

# Spend guardrails handed to `claude --max-budget-usd`. They replace the old
# max_tokens caps: with the CLI the useful bound is money, not tokens. Set a
# stage to 0 to drop the flag.
PLAN_BUDGET_USD = _float("CLAUDE_PLAN_BUDGET_USD", "0.50")
TESTGEN_BUDGET_USD = _float("CLAUDE_TESTGEN_BUDGET_USD", "0.50")
GENERATE_BUDGET_USD = _float("CLAUDE_GENERATE_BUDGET_USD", "2.00")
REPAIR_BUDGET_USD = _float("CLAUDE_REPAIR_BUDGET_USD", "2.00")

# ------------------------------------------------------------------ deadline
# Re-tuned for CLI latency: a `claude -p` turn costs process start-up plus
# thinking time, so the thresholds that used to fit a fast HTTP call now have
# to cover a whole CLI round trip.
RESERVE_S = 20.0
LATE_PHASE_S = 70.0
MIN_REPAIR_S = 60.0
# Adversarial tests are optional: only spend a CLI round trip on them while
# there is still room for a repair afterwards.
ADVERSARIAL_MIN_S = 90.0

MAX_REPAIRS = 3
VERIFY_TIMEOUT_S = 3.0
BENCHMARK_TIMEOUT_S = 1.5
