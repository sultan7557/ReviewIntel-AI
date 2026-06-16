import os

from dotenv import load_dotenv

load_dotenv()

AI_PROVIDER = os.getenv("AI_PROVIDER", "groq").strip().lower()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")
GEMINI_STEP_DELAY = float(os.getenv("GEMINI_STEP_DELAY", "8"))
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "3"))
GEMINI_MAX_RETRY_DELAY = float(os.getenv("GEMINI_MAX_RETRY_DELAY", "30"))
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Each successful "Run Analysis" makes exactly this many LLM API calls (sequential, not parallel).
PIPELINE_API_CALLS = 4

SUPPORTED_PROVIDERS = ("claude", "gemini", "groq")


def get_provider() -> str:
    if AI_PROVIDER not in SUPPORTED_PROVIDERS:
        raise RuntimeError(
            f"Unsupported AI_PROVIDER '{AI_PROVIDER}'. "
            f"Use one of: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    return AI_PROVIDER


def validate_provider_credentials() -> None:
    provider = get_provider()
    if provider == "claude":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    elif provider == "gemini":
        if not os.getenv("GEMINI_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY is not set. Add it to your .env file.")
    elif provider == "groq":
        if not os.getenv("GROQ_API_KEY"):
            raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file.")
