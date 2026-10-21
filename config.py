
"""Telegram bot sozlamalari."""
import os
from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _required(name: str) -> str:
    value = _get(name)
    if not value:
        raise SystemExit(
            f"❌ {name} topilmadi. Environment Variables sozlamalarini tekshiring."
        )
    return value


def _id_set(name: str) -> set[int]:
    result = set()
    raw = _get(name)

    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        if not item.lstrip("-").isdigit():
            raise SystemExit(
                f"❌ {name} noto'g'ri: {item}"
            )
        result.add(int(item))

    return result


# Majburiy API kalitlari
TELEGRAM_TOKEN = _required("TELEGRAM_TOKEN")
GROQ_API_KEY = _required("GROQ_API_KEY")

# AI provayderlari
GEMINI_API_KEY = _get("GEMINI_API_KEY")
GEMINI_MODEL = _get("GEMINI_MODEL", "gemini-2.5-flash")

OPENROUTER_API_KEY = _get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = _get("OPENROUTER_MODEL", "openrouter/free")
OPENROUTER_VISION_MODEL = _get("OPENROUTER_VISION_MODEL")

PROVIDERS = ("groq", "openrouter", "gemini")

CHAT_PROVIDER = _get("CHAT_PROVIDER", "groq").lower()
FILE_PROVIDER = _get(
    "FILE_PROVIDER",
    "openrouter" if OPENROUTER_API_KEY else "groq"
).lower()

for name, value in (
    ("CHAT_PROVIDER", CHAT_PROVIDER),
    ("FILE_PROVIDER", FILE_PROVIDER),
):
    if value not in PROVIDERS:
        raise SystemExit(
            f"❌ {name} noto'g'ri. "
            "Qiymat: groq, openrouter yoki gemini."
        )

# Cloudflare rasm yaratish va tahrirlash
CF_ACCOUNT_ID = _get("CF_ACCOUNT_ID")
CF_API_TOKEN = _get("CF_API_TOKEN")
IMAGES_ENABLED = bool(CF_ACCOUNT_ID and CF_API_TOKEN)

IMAGE_MODEL = _get(
    "IMAGE_MODEL",
    "@cf/black-forest-labs/flux-1-schnell"
)
EDIT_MODEL = _get(
    "EDIT_MODEL",
    "@cf/black-forest-labs/flux-2-klein-4b"
)

# Groq modellari
CHAT_MODEL = _get("CHAT_MODEL", "openai/gpt-oss-120b")
VISION_MODEL = _get("VISION_MODEL", "qwen/qwen3.8-27b")
CLASSIFIER_MODEL = _get("CLASSIFIER_MODEL", "openai/gpt-oss-20b")
STT_MODEL = _get("STT_MODEL", "whisper-large-v3-turbo")
STT_LANGUAGE = _get("STT_LANGUAGE")
REASONING_EFFORT = _get("REASONING_EFFORT", "low")

CHAT_MAX_TOKENS = max(128, _int("CHAT_MAX_TOKENS", 2048))

# Ruxsatlar va limitlar
ALLOWED_USERS = _id_set("ALLOWED_USERS")
IMAGE_DAILY_LIMIT = max(0, _int("IMAGE_DAILY_LIMIT", 5))
FILE_DAILY_LIMIT = max(0, _int("FILE_DAILY_LIMIT", 10))
RATE_LIMIT_SECONDS = max(0.0, _float("RATE_LIMIT_SECONDS", 3.0))
VOICE_MAX_SECONDS = max(1, _int("VOICE_MAX_SECONDS", 120))

# Xotira
HISTORY_LIMIT = max(2, _int("HISTORY_LIMIT", 10))
HISTORY_LIMIT -= HISTORY_LIMIT % 2

HISTORY_MSG_CHARS = max(
    100, _int("HISTORY_MSG_CHARS", 1500)
)

# Keshlar — state.py ishlatadigan sozlamalar
IMAGE_CACHE = max(1, _int("IMAGE_CACHE", 100))
FILE_CACHE = max(1, _int("FILE_CACHE", 500))
DECK_CACHE = max(1, _int("DECK_CACHE", 100))

# Texnik sozlamalar
WORKERS = max(1, _int("WORKERS", 8))
REQUEST_TIMEOUT = max(5, _int("REQUEST_TIMEOUT", 60))
IMAGE_TIMEOUT = max(5, _int("IMAGE_TIMEOUT", 120))

TELEGRAM_MAX_CHARS = 3500
HEALTH_PORT = max(
    0, _int("PORT", _int("HEALTH_PORT", 8080))
)
