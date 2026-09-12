"""LLM access: Gemini cheap, Groq codegen, OpenAI fallback."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from . import config


@dataclass
class ModelReply:
    text: str
    model: str
    tier: str
    fallback: bool
    prompt_tokens: int = 0
    completion_tokens: int = 0
    provider: str = ""


@dataclass
class UsageMeter:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    by_provider: dict[str, int] = field(default_factory=dict)

    def add(self, reply: ModelReply) -> None:
        self.prompt_tokens += reply.prompt_tokens
        self.completion_tokens += reply.completion_tokens
        self.calls += 1
        self.by_tier[reply.tier or "unknown"] = self.by_tier.get(reply.tier or "unknown", 0) + 1
        provider = reply.provider or "unknown"
        self.by_provider[provider] = self.by_provider.get(provider, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "calls": self.calls,
            "by_tier": dict(self.by_tier),
            "by_provider": dict(self.by_provider),
        }


USAGE = UsageMeter()


def reset_usage() -> None:
    USAGE.prompt_tokens = 0
    USAGE.completion_tokens = 0
    USAGE.calls = 0
    USAGE.by_tier.clear()
    USAGE.by_provider.clear()


def load_env(path=config.ENV_PATH) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def _env_key(*names: str) -> str:
    env = load_env()
    for name in names:
        value = env.get(name)
        if value:
            return value
    return ""


def _groq_keys() -> list[str]:
    env = load_env()
    return [env[name] for name in ("GROQ_API_KEY", "GROQ_API_KEY_2") if env.get(name)]


def model_for(tier: str) -> str:
    if tier == "cheap":
        return config.CHEAP_MODEL
    if tier == "strong":
        return config.STRONG_MODEL
    return config.NORMAL_MODEL


def provider_for(tier: str) -> str:
    if tier == "cheap":
        return config.CHEAP_PROVIDER
    if tier == "strong":
        return config.STRONG_PROVIDER
    return config.NORMAL_PROVIDER


def choose_tier(stage: str, difficulty: str, remaining_s: float, repair_attempt: int = 0) -> str:
    """Cost-aware routing. Codegen never uses the Gemini cheap tier."""
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


def _extract_openai_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or [{}]
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    reasoning = message.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning
    return ""


def _openai_compatible(
    url: str,
    keys: list[str],
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
    extra: dict[str, Any] | None = None,
    user_agent: str = "",
) -> ModelReply:
    last_error = None
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if extra:
        body.update(extra)
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers_base = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if user_agent:
        headers_base["User-Agent"] = user_agent

    if not keys:
        raise RuntimeError(f"no API key for {url}")
    for attempt in range(4):
        data = json.dumps(body).encode()
        key = keys[attempt % len(keys)]
        headers = dict(headers_base)
        headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.load(response)
            usage = payload.get("usage") or {}
            return ModelReply(
                text=_extract_openai_text(payload),
                model=body.get("model") or model,
                tier="",
                fallback=False,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:240]
            last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
            if exc.code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            if exc.code == 401:
                continue
            if exc.code == 400 and extra and "reasoning_effort" in extra:
                extra.pop("reasoning_effort", None)
                body.pop("reasoning_effort", None)
                continue
            raise last_error from exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1.0)
            continue
    raise RuntimeError(f"chat request failed: {last_error}")


def _chat_groq(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
    reasoning_effort: str,
) -> ModelReply:
    reply = _openai_compatible(
        config.GROQ_CHAT_URL,
        _groq_keys(),
        messages,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=json_mode,
        extra={"reasoning_effort": reasoning_effort},
        user_agent=config.USER_AGENT,
    )
    reply.provider = "groq"
    return reply


def _chat_openai(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
) -> ModelReply:
    key = _env_key("OPENAI_API_KEY")
    reply = _openai_compatible(
        config.OPENAI_CHAT_URL,
        [key] if key else [],
        messages,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=json_mode,
    )
    reply.provider = "openai"
    return reply


def _gemini_parts(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, Any]]]:
    system = ""
    contents = []
    for message in messages:
        role = message.get("role") or "user"
        text = message.get("content") or ""
        if role == "system":
            system = (system + "\n" + text).strip() if system else text
            continue
        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": text}]})
    if not contents:
        contents = [{"role": "user", "parts": [{"text": system or " "}]}]
        system = ""
    return system, contents


def _chat_gemini(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
) -> ModelReply:
    key = _env_key("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("no Gemini API key")
    system, contents = _gemini_parts(messages)
    url = config.GEMINI_GENERATE_URL.format(model=model) + "?" + urlencode({"key": key})
    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }
    if system:
        body["system_instruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:240]
        raise RuntimeError(f"Gemini HTTP {exc.code}: {detail}") from exc
    candidates = payload.get("candidates") or [{}]
    parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
    text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
    usage = payload.get("usageMetadata") or {}
    return ModelReply(
        text=text.strip(),
        model=model,
        tier="",
        fallback=False,
        prompt_tokens=int(usage.get("promptTokenCount") or 0),
        completion_tokens=int(usage.get("candidatesTokenCount") or 0),
        provider="gemini",
    )


def _call_provider(
    provider: str,
    messages: list[dict[str, str]],
    *,
    tier: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
) -> ModelReply:
    effort = "medium" if tier == "strong" else "low"
    if provider == "gemini":
        return _chat_gemini(
            messages,
            model=config.CHEAP_MODEL if tier == "cheap" else config.CHEAP_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
        )
    if provider == "openai":
        return _chat_openai(
            messages,
            model=config.FALLBACK_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
        )
    model = model_for(tier) if tier != "cheap" else config.NORMAL_MODEL
    return _chat_groq(
        messages,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=json_mode,
        reasoning_effort=effort,
    )


def chat(
    messages: list[dict[str, str]],
    *,
    tier: str = "cheap",
    max_tokens: int = 800,
    temperature: float = 0.1,
    json_mode: bool = False,
    fallback_text: str = "",
) -> ModelReply:
    requested = provider_for(tier)
    chain = [requested]
    if "groq" not in chain:
        chain.append("groq")
    if config.FALLBACK_PROVIDER not in chain:
        chain.append(config.FALLBACK_PROVIDER)

    last_error = None
    for index, provider in enumerate(chain):
        try:
            reply = _call_provider(
                provider,
                messages,
                tier=tier,
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=json_mode,
            )
            reply.tier = tier
            if not reply.text.strip():
                last_error = RuntimeError(f"{provider} empty content")
                continue
            reply.fallback = index > 0
            USAGE.add(reply)
            return reply
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    reply = ModelReply(
        text=fallback_text,
        model="local-fallback",
        tier=tier,
        fallback=True,
        provider="local",
    )
    USAGE.add(reply)
    return reply


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
