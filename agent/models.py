"""Model access through the Claude Code CLI.

Every tier is a Claude model reached by shelling out to `claude -p`:

    cheap   -> claude-haiku-4-5   plan, adversarial test ideas
    normal  -> claude-sonnet-5    Python / Rust generation, repair
    strong  -> claude-opus-5      hard problems, escalated repair

The CLI owns authentication, overload fallback (`--fallback-model`), prompt
caching and session storage, so this module only has to build an argv, feed
the prompt on stdin, and read back the single JSON result object.

Calls are hardened for unattended use:

* `--tools ""` so the child cannot touch the filesystem or the network,
* `--permission-prompts none` so nothing can block on a prompt,
* `--safe-mode` + `--strict-mcp-config` so a developer's CLAUDE.md, skills,
  hooks or MCP servers cannot change what this agent generates,
* an explicit `--session-id` per call, because a parent Claude Code process
  exports CLAUDE_CODE_SESSION_ID and the child would otherwise inherit it,
* a private empty working directory, so no project context leaks in.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import config

load_env = config.load_env


@dataclass
class ModelReply:
    text: str
    model: str
    tier: str
    fallback: bool
    prompt_tokens: int = 0
    completion_tokens: int = 0
    provider: str = ""
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    session_id: str = ""
    structured: Optional[dict[str, Any]] = None
    duration_ms: int = 0
    error: str = ""


@dataclass
class UsageMeter:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    by_provider: dict[str, int] = field(default_factory=dict)
    by_model: dict[str, int] = field(default_factory=dict)

    def add(self, reply: ModelReply) -> None:
        self.prompt_tokens += reply.prompt_tokens
        self.completion_tokens += reply.completion_tokens
        self.cache_read_tokens += reply.cache_read_tokens
        self.cache_creation_tokens += reply.cache_creation_tokens
        self.cost_usd = round(self.cost_usd + reply.cost_usd, 6)
        self.calls += 1
        tier = reply.tier or "unknown"
        self.by_tier[tier] = self.by_tier.get(tier, 0) + 1
        provider = reply.provider or "unknown"
        self.by_provider[provider] = self.by_provider.get(provider, 0) + 1
        model = reply.model or "unknown"
        self.by_model[model] = self.by_model.get(model, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "calls": self.calls,
            "by_tier": dict(self.by_tier),
            "by_provider": dict(self.by_provider),
            "by_model": dict(self.by_model),
        }


USAGE = UsageMeter()


def reset_usage() -> None:
    USAGE.prompt_tokens = 0
    USAGE.completion_tokens = 0
    USAGE.cache_read_tokens = 0
    USAGE.cache_creation_tokens = 0
    USAGE.cost_usd = 0.0
    USAGE.calls = 0
    USAGE.by_tier.clear()
    USAGE.by_provider.clear()
    USAGE.by_model.clear()


# --------------------------------------------------------------- routing


def model_for(tier: str) -> str:
    if tier == "cheap":
        return config.CHEAP_MODEL
    if tier == "strong":
        return config.STRONG_MODEL
    return config.NORMAL_MODEL


def provider_for(tier: str) -> str:  # noqa: ARG001 - one provider, kept for meta
    return config.PROVIDER


def model_chain(tier: str) -> list[str]:
    """Requested model first, then cheaper survivors, without duplicates."""
    chain: list[str] = []
    for model in (model_for(tier), config.FALLBACK_MODEL, config.NORMAL_MODEL, config.CHEAP_MODEL):
        if model and model not in chain:
            chain.append(model)
    return chain


def effort_for(tier: str, remaining_s: float = 0.0) -> str:
    """`claude --effort` for a tier, stepped down when the clock is short."""
    effort = config.EFFORT_BY_TIER.get(tier, "low")
    ladder = config.EFFORT_LADDER
    if remaining_s and remaining_s < config.EFFORT_DOWNGRADE_S and effort in ladder:
        effort = ladder[max(0, ladder.index(effort) - 1)]
    return effort


def choose_tier(stage: str, difficulty: str, remaining_s: float, repair_attempt: int = 0) -> str:
    """Cost-aware routing. Codegen never uses the cheap tier."""
    if stage in {"plan", "tests"}:
        return "cheap"
    if stage == "generate":
        if difficulty == "hard" and remaining_s >= 120:
            return "strong"
        return "normal"
    if stage == "repair":
        if repair_attempt >= 1 or difficulty == "hard":
            return "strong" if remaining_s >= 80 else "normal"
        return "normal"
    return "cheap"


def call_timeout(remaining_s: float) -> float:
    """How long one CLI call may run without eating the write reserve."""
    usable = float(remaining_s or 0.0) - config.RESERVE_S
    if usable <= 0:
        return 0.0
    return min(config.CLI_TIMEOUT_S, usable)


# --------------------------------------------------------------- transport

_JSON_ONLY = (
    "Reply with a single JSON object and nothing else: no prose, no markdown "
    "fence, no trailing commentary."
)

_SCRUBBED_ENV = (
    # A parent Claude Code process exports these; the child must not inherit
    # a session id or an effort level we did not choose.
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CLAUDE_EFFORT",
)

_PASSTHROUGH_PREFIXES = ("ANTHROPIC_", "AWS_", "CLAUDE_CODE_USE_")

_WORKDIR: Optional[str] = None
_CLI_PATH: Optional[str] = None
# Session ids this process created. Only these may be resumed, so a session id
# inherited from a parent Claude Code process can never be pulled into a call.
_MINTED: set[str] = set()


def resumable_session(session_id: str) -> bool:
    return bool(session_id) and session_id in _MINTED


def bin_candidates(name: str, windows: bool = os.name == "nt") -> list[str]:
    """Names to look for on PATH, most launchable first.

    On Windows `npm install -g` leaves a `claude.cmd` shim next to an
    extensionless shell script. CreateProcess does not apply PATHEXT and
    cannot run the shell script, so the shim has to win.
    """
    if not windows or os.path.splitext(name)[1]:
        return [name]
    return [name + ext for ext in (".cmd", ".exe", ".bat")] + [name]


def cli_path(refresh: bool = False) -> str:
    """Absolute path of the Claude binary, resolved once, or "" if missing.

    The resolved path is what gets executed: handing Popen the bare name works
    on POSIX but fails on Windows, where the thing on PATH is `claude.cmd`.
    """
    global _CLI_PATH
    if refresh or _CLI_PATH is None:
        _CLI_PATH = ""
        for candidate in bin_candidates(config.CLAUDE_BIN):
            found = shutil.which(candidate)
            if found:
                _CLI_PATH = found
                break
    return _CLI_PATH


def cli_available(refresh: bool = False) -> bool:
    """True when the Claude CLI is on PATH."""
    return bool(cli_path(refresh))


def _workdir() -> str:
    """An empty directory, so no CLAUDE.md or repo file reaches the model."""
    global _WORKDIR
    if config.CLI_WORKDIR:
        path = Path(config.CLI_WORKDIR).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return str(path)
    if _WORKDIR is None:
        _WORKDIR = tempfile.mkdtemp(prefix="challengebox-cli-")
        atexit.register(shutil.rmtree, _WORKDIR, True)
    return _WORKDIR


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in _SCRUBBED_ENV:
        env.pop(name, None)
    for key, value in config.load_env().items():
        if value and key.startswith(_PASSTHROUGH_PREFIXES):
            env[key] = value
    return env


def _split_messages(messages: list[dict[str, str]]) -> tuple[str, str]:
    """Collapse OpenAI-style messages into (system prompt, user prompt)."""
    system_parts: list[str] = []
    user_parts: list[str] = []
    for message in messages or []:
        role = (message.get("role") or "user").lower()
        text = message.get("content") or ""
        if not text:
            continue
        if role == "system":
            system_parts.append(text)
        elif role == "assistant":
            user_parts.append(f"[previous answer]\n{text}")
        else:
            user_parts.append(text)
    return "\n\n".join(system_parts).strip(), "\n\n".join(user_parts).strip()


def build_argv(
    *,
    model: str,
    system: str,
    effort: str,
    json_schema: Optional[dict[str, Any]] = None,
    budget_usd: float = 0.0,
    fallback_models: Optional[list[str]] = None,
    resume: str = "",
    session_id: str = "",
    resumable: bool = False,
) -> list[str]:
    """The full `claude` command line for one non-interactive call."""
    argv = [
        cli_path() or config.CLAUDE_BIN,
        "--print",
        "--output-format",
        "json",
        "--model",
        model,
        "--tools",
        config.CLI_TOOLS,
        "--permission-prompts",
        "none",
        "--strict-mcp-config",
        "--effort",
        effort,
    ]
    if config.CLI_SAFE_MODE:
        argv.append("--safe-mode")
    if system:
        argv += ["--system-prompt", system]
    if fallback_models:
        argv += ["--fallback-model", ",".join(fallback_models)]
    if budget_usd and budget_usd > 0:
        argv += ["--max-budget-usd", f"{budget_usd:.2f}"]
    if json_schema:
        argv += ["--json-schema", json.dumps(json_schema, separators=(",", ":"))]
    if resume:
        # Fork so a rejected repair cannot corrupt the branch we came from,
        # and render the new system prompt instead of the recorded one.
        argv += ["--resume", resume, "--fork-session", "--system-prompt-snapshot", "off"]
    # Always name the session ourselves: a parent Claude Code process exports
    # its own session id, and an inherited one must never be resumable.
    argv += ["--session-id", session_id or str(uuid.uuid4())]
    if not resumable:
        argv.append("--no-session-persistence")
    return argv


def parse_result(stdout: str) -> Optional[dict[str, Any]]:
    """Pull the result object out of CLI stdout, ignoring any noise lines."""
    found: Optional[dict[str, Any]] = None
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "result":
            found = payload
    if found is not None:
        return found
    try:
        payload = json.loads((stdout or "").strip())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def payload_error(payload: dict[str, Any]) -> str:
    """Empty string when the CLI really did answer."""
    if payload.get("is_error"):
        status = payload.get("api_error_status")
        detail = str(payload.get("result") or "cli reported an error")[:200]
        return f"api_error status={status}: {detail}"
    terminal = payload.get("terminal_reason")
    if terminal and terminal not in {"completed", "success"}:
        return f"terminal_reason={terminal}"
    subtype = payload.get("subtype")
    if subtype and subtype != "success":
        return f"subtype={subtype}"
    return ""


_LOG_LOCK = threading.Lock()


def _log_call(
    argv: list[str],
    system: str,
    user: str,
    payload: Optional[dict[str, Any]],
    error: str,
    elapsed_s: float,
) -> None:
    """Append one call - argv, both prompts, outcome - to CLAUDE_PROMPT_LOG."""
    path = config.PROMPT_LOG
    if not path:
        return
    model = argv[argv.index("--model") + 1] if "--model" in argv else "?"
    effort = argv[argv.index("--effort") + 1] if "--effort" in argv else "?"
    if error:
        outcome = f"FAILED after {elapsed_s:.1f}s - {error}"
        result = ""
    else:
        cost = float((payload or {}).get("total_cost_usd") or 0.0)
        result = str((payload or {}).get("result") or "")
        outcome = f"ok in {elapsed_s:.1f}s - ${cost:.4f}, {len(result)} chars"
    lines = [
        "=" * 78,
        f"CALL  model={model}  effort={effort}  {outcome}",
        "=" * 78,
        "--- argv ---",
        json.dumps(argv, indent=2),
        "--- system prompt ---",
        system,
        "--- user prompt (child stdin) ---",
        user,
        "--- reply ---",
        result or "(none)",
        "",
    ]
    try:
        with _LOG_LOCK, open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass  # logging must never take a run down


def _invoke(argv: list[str], prompt: str, timeout_s: float) -> tuple[Optional[dict[str, Any]], str]:
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Without these, text mode uses the locale codec: cp1252 on a
            # stock Windows box, where the node CLI's UTF-8 arrives mangled
            # ("∈" -> "âˆˆ") or kills the reader thread outright.
            encoding="utf-8",
            errors="replace",
            cwd=_workdir(),
            env=_child_env(),
            start_new_session=True,
        )
    except FileNotFoundError:
        return None, f"cannot execute {argv[0]!r} (set CLAUDE_BIN to its full path)"
    except Exception as exc:  # noqa: BLE001 - a bad argv raises ValueError on Windows
        return None, f"spawn failed: {type(exc).__name__}: {exc}"

    try:
        stdout, stderr = process.communicate(input=prompt, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill(process)
        return None, f"timeout after {timeout_s:.0f}s"
    except Exception as exc:  # noqa: BLE001 - never leave the child running
        _kill(process)
        return None, f"transport failure: {type(exc).__name__}: {exc}"

    payload = parse_result(stdout)
    if payload is None:
        detail = (stderr or stdout or "no output").strip().splitlines()
        return None, f"exit={process.returncode} unparsable output: {detail[-1][:200] if detail else ''}"
    error = payload_error(payload)
    if error:
        return payload, error
    if process.returncode not in (0, None):
        return payload, f"exit={process.returncode}"
    return payload, ""


def _kill(process: subprocess.Popen) -> None:
    # killpg takes the whole tree with it, but it only exists on POSIX.
    try:
        if hasattr(os, "killpg") and hasattr(os, "getpgid"):
            os.killpg(os.getpgid(process.pid), 9)
        else:
            process.kill()
    except Exception:  # noqa: BLE001
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass
    try:
        process.communicate(timeout=5)
    except Exception:  # noqa: BLE001
        pass


def _sum_model_usage(payload: dict[str, Any]) -> dict[str, int]:
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    for entry in (payload.get("modelUsage") or {}).values():
        if not isinstance(entry, dict):
            continue
        totals["input"] += int(entry.get("inputTokens") or 0)
        totals["output"] += int(entry.get("outputTokens") or 0)
        totals["cache_read"] += int(entry.get("cacheReadInputTokens") or 0)
        totals["cache_creation"] += int(entry.get("cacheCreationInputTokens") or 0)
    if any(totals.values()):
        return totals
    usage = payload.get("usage") or {}
    return {
        "input": int(usage.get("input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "cache_creation": int(usage.get("cache_creation_input_tokens") or 0),
    }


def _reply_from_payload(payload: dict[str, Any], requested: str, tier: str) -> ModelReply:
    used = list((payload.get("modelUsage") or {}).keys())
    structured = payload.get("structured_output")
    if not isinstance(structured, dict):
        structured = None
    text = str(payload.get("result") or "").strip()
    if not text and structured is not None:
        text = json.dumps(structured)
    totals = _sum_model_usage(payload)
    return ModelReply(
        text=text,
        model=used[0] if len(used) == 1 else requested,
        tier=tier,
        fallback=False,
        prompt_tokens=totals["input"],
        completion_tokens=totals["output"],
        provider=config.PROVIDER,
        cache_read_tokens=totals["cache_read"],
        cache_creation_tokens=totals["cache_creation"],
        cost_usd=float(payload.get("total_cost_usd") or 0.0),
        session_id=str(payload.get("session_id") or ""),
        structured=structured,
        duration_ms=int(payload.get("duration_ms") or 0),
    )


def _local_reply(tier: str, fallback_text: str, reason: str) -> ModelReply:
    reply = ModelReply(
        text=fallback_text,
        model="local-fallback",
        tier=tier,
        fallback=True,
        provider="local",
        error=reason,
    )
    USAGE.add(reply)
    return reply


def chat(
    messages: list[dict[str, str]],
    *,
    tier: str = "cheap",
    remaining_s: float = 0.0,
    json_mode: bool = False,
    json_schema: Optional[dict[str, Any]] = None,
    budget_usd: float = 0.0,
    timeout_s: Optional[float] = None,
    resume_session: str = "",
    resumable: bool = False,
    fallback_text: str = "",
) -> ModelReply:
    """One Claude CLI turn, with model fallback and a hard time budget."""
    system, user = _split_messages(messages)
    if json_mode and not json_schema:
        system = f"{system}\n\n{_JSON_ONLY}".strip()

    total_s = call_timeout(remaining_s) if timeout_s is None else float(timeout_s)
    if not cli_available():
        return _local_reply(tier, fallback_text, f"{config.CLAUDE_BIN} not installed")
    if total_s < config.CLI_MIN_CALL_S:
        return _local_reply(tier, fallback_text, "not enough time for a model call")

    effort = effort_for(tier, remaining_s)
    chain = model_chain(tier)
    schema = json_schema
    resume = resume_session if config.REUSE_SESSION and resumable_session(resume_session) else ""
    keep = bool(resumable or resume) and config.REUSE_SESSION
    started = time.monotonic()
    errors: list[str] = []

    for index, model in enumerate(chain):
        attempts = max(1, config.CLI_ATTEMPTS)
        attempt = 0
        while attempt < attempts:
            attempt += 1
            left = total_s - (time.monotonic() - started)
            if left < config.CLI_MIN_CALL_S:
                return _local_reply(tier, fallback_text, "; ".join(errors[-2:]) or "out of time")
            # Leave the next model a usable window. Without this the first model
            # spends the whole pool on a timeout and every fallback dies on the
            # clock too, which is how a run ends up with a local skeleton.
            window = left
            if index + 1 < len(chain):
                window = max(config.CLI_MIN_CALL_S, left * config.CLI_FIRST_ATTEMPT_SHARE)
            window = min(left, window)
            session_id = str(uuid.uuid4())
            argv = build_argv(
                model=model,
                system=system,
                effort=effort,
                json_schema=schema,
                budget_usd=budget_usd,
                fallback_models=chain[index + 1 :],
                resume=resume,
                session_id=session_id,
                resumable=keep,
            )
            call_started = time.monotonic()
            payload, error = _invoke(argv, user, window)
            _log_call(argv, system, user, payload, error, time.monotonic() - call_started)
            if payload is not None:
                reply = _reply_from_payload(payload, model, tier)
                if not error and not reply.text.strip():
                    error = "empty result"
                if not error:
                    reply.fallback = index > 0 or reply.model != model
                    if keep:
                        _MINTED.add(session_id)
                        reply.session_id = session_id
                    else:
                        reply.session_id = ""
                    USAGE.add(reply)
                    return reply
                # A failed turn still burned tokens; keep the cost books honest.
                reply.fallback = True
                reply.error = error
                reply.session_id = ""
                USAGE.add(reply)
            errors.append(f"{model}: {error}")
            if schema is not None and "schema" in error.lower():
                schema = None  # structured output refused; fall back to prompted JSON
                attempts += 1
                continue
            if resume:
                resume = ""  # stale session id; retry as a fresh conversation
                attempts += 1
                continue
            time.sleep(1.0)

    return _local_reply(tier, fallback_text, "; ".join(errors[-3:]) or "all models failed")


def probe(timeout_s: float = 90.0) -> tuple[bool, str]:
    """One tiny call that proves the CLI is installed and authenticated."""
    if not cli_available(refresh=True):
        return False, (
            f"{config.CLAUDE_BIN} is not on PATH - install Claude Code, or set "
            "CLAUDE_BIN to the full path of the binary"
        )
    reply = chat(
        [
            {"role": "system", "content": "Reply with the requested text only."},
            {"role": "user", "content": "Reply with exactly: OK"},
        ],
        tier="cheap",
        timeout_s=timeout_s,
    )
    if reply.provider == "local":
        return False, reply.error or "no reply"
    return True, f"{reply.model} replied {reply.text[:40]!r} (${reply.cost_usd:.4f})"


# --------------------------------------------------------------- extraction


def extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


def reply_json(reply: ModelReply) -> dict[str, Any]:
    """Structured output when the CLI validated a schema, else parsed text."""
    if isinstance(reply.structured, dict) and reply.structured:
        return reply.structured
    return extract_json(reply.text)


def extract_code(text: str, language: str = "python") -> str:
    lang = (language or "python").lower()
    aliases = "rust|rs" if lang == "rust" else "python"
    blocks = re.findall(rf"```(?:{aliases})\n(.*?)```", text or "", flags=re.S | re.I)
    if not blocks:
        blocks = re.findall(r"```(?:python|rust|rs)?\n(.*?)```", text or "", flags=re.S)
    if blocks:
        return max(blocks, key=len).strip()
    return (text or "").strip()


def extract_python(text: str) -> str:
    return extract_code(text, "python")
