"""Runtime knobs for the Phase 4 agent.

Every model call goes through the Claude Code CLI (`claude -p`), so there is
no provider SDK, no HTTP client, and no per-provider API key in this project.
Authentication, overload retries, and prompt caching are the CLI's job.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBLEMS_DIR = ROOT / "problems"
SOLUTIONS_DIR = ROOT / "solutions"
SAMPLES_DIR = ROOT / "samples"
ENV_PATH = ROOT / ".env"

PHASE = 4
RUNTIME = "claude-cli"


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """Parse a tiny KEY=value .env file. Missing file means empty settings."""
    env: dict[str, str] = {}
    try:
        raw_text = path.read_text()
    except OSError:
        return env
    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def setting(name: str, default: str) -> str:
    """Process environment wins, then .env, then the built-in default."""
    value = os.environ.get(name)
    if value:
        return value
    value = load_env().get(name)
    return value if value else default


def _flag(name: str, default: bool) -> bool:
    raw = setting(name, "1" if default else "0").strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


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
CHEAP_MODEL = setting("CLAUDE_CHEAP_MODEL", "claude-haiku-4-5-20251001")
# Python / Rust generation and repair.
NORMAL_MODEL = setting("CLAUDE_NORMAL_MODEL", "claude-sonnet-5")
# Hard problems and escalated repairs.
STRONG_MODEL = setting("CLAUDE_STRONG_MODEL", "claude-opus-5")
# Handed to `claude --fallback-model` when the requested model is overloaded.
FALLBACK_MODEL = setting("CLAUDE_FALLBACK_MODEL", NORMAL_MODEL)

# `claude --effort` replaces the old provider-specific reasoning_effort knob.
EFFORT_BY_TIER = {"cheap": "low", "normal": "medium", "strong": "high"}
EFFORT_LADDER = ["low", "medium", "high"]
# Below this much wall clock, drop one effort step so a slow think cannot eat
# the deadline.
EFFORT_DOWNGRADE_S = 150.0

CLI_TIMEOUT_S = float(setting("CLAUDE_CLI_TIMEOUT_S", "120"))
CLI_MIN_CALL_S = float(setting("CLAUDE_CLI_MIN_CALL_S", "20"))
CLI_ATTEMPTS = int(setting("CLAUDE_CLI_ATTEMPTS", "2"))
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

# Spend guardrails handed to `claude --max-budget-usd`. They replace the old
# max_tokens caps: with the CLI the useful bound is money, not tokens. Set a
# stage to 0 to drop the flag.
PLAN_BUDGET_USD = float(setting("CLAUDE_PLAN_BUDGET_USD", "0.50"))
TESTGEN_BUDGET_USD = float(setting("CLAUDE_TESTGEN_BUDGET_USD", "0.50"))
GENERATE_BUDGET_USD = float(setting("CLAUDE_GENERATE_BUDGET_USD", "2.00"))
REPAIR_BUDGET_USD = float(setting("CLAUDE_REPAIR_BUDGET_USD", "2.00"))

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
