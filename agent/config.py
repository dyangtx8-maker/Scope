"""Runtime knobs for the Phase 3 agent."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBLEMS_DIR = ROOT / "problems"
SOLUTIONS_DIR = ROOT / "solutions"
SAMPLES_DIR = ROOT / "samples"
ENV_PATH = ROOT / ".env"

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

PHASE = 3

# cheap = Gemini lite (plan / tests). Codegen stays on Groq.
CHEAP_PROVIDER = "gemini"
CHEAP_MODEL = "gemini-2.5-flash-lite"
NORMAL_PROVIDER = "groq"
NORMAL_MODEL = "openai/gpt-oss-120b"
STRONG_PROVIDER = "groq"
STRONG_MODEL = "openai/gpt-oss-120b"
FALLBACK_PROVIDER = "openai"
FALLBACK_MODEL = "gpt-4.1-mini"

RESERVE_S = 20.0
LATE_PHASE_S = 50.0
MIN_REPAIR_S = 35.0

MAX_REPAIRS = 3
VERIFY_TIMEOUT_S = 3.0
BENCHMARK_TIMEOUT_S = 1.5
PLAN_MAX_TOKENS = 1200
GENERATE_MAX_TOKENS = 8000
REPAIR_MAX_TOKENS = 8000
TESTGEN_MAX_TOKENS = 1400

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
