
"""Telegram bot konfiguratsiyasi.

Qiymatlar .env yoki hosting Environment Variables orqali olinadi.
Maxfiy API kalitlarini kodga yozmang va GitHub'ga joylamang.
"""
import os
from dotenv import load_dotenv

load_dotenv()


# ================================================================
# YORDAMCHI FUNKSIYALAR
# ================================================================

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
            f"❌ {name} topilmadi. Environment Variables yoki .env faylini tekshiring."
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
            raise SystemExit(f"❌ {name} noto'g'ri: {item}")
        result.add(int(item))

    return result


# ================================================================
# TELEGRAM VA ASOSIY API
# ================================================================

TELEGRAM_TOKEN = _required("TELEGRAM_TOKEN")
APP_PASSWORD = _get("APP_PASSWORD")

# Hozirgi llm.py asosiy provayder sifatida Groqni kutadi.
GROQ_API_KEY = _get("GROQ_API_KEY")


# ================================================================
# BRAVE SEARCH — INTERNETDAN QIDIRISH
# ================================================================

BRAVE_API_KEY = _get("BRAVE_API_KEY")

# Brave API natijalarining hududi va tili.
# Country: US, GB, DE, FR va boshqa Brave qo'llaydigan kodlar.
BRAVE_SEARCH_COUNTRY = _get("BRAVE_SEARCH_COUNTRY", "US").upper()
BRAVE_SEARCH_LANG = _get("BRAVE_SEARCH_LANG", "en").lower()

# Natijalar soni: 1–10.
BRAVE_SEARCH_COUNT = max(1, min(_int("BRAVE_SEARCH_COUNT", 5), 10))

# Qidiruv timeout: 5–30 soniya.
BRAVE_SEARCH_TIMEOUT = max(
    5,
    min(_int("BRAVE_SEARCH_TIMEOUT", 12), 30),
)

BRAVE_SEARCH_ENABLED = bool(BRAVE_API_KEY)


# ================================================================
# GEMINI
# ================================================================

GEMINI_API_KEY = _get("GEMINI_API_KEY")
GEMINI_MODEL = _get("GEMINI_MODEL", "gemini-2.5-flash")


# ================================================================
# OPENROUTER — KALITLAR VA MODELLAR
# ================================================================

OPENROUTER_API_KEY_1 = _get("OPENROUTER_API_KEY_1")
OPENROUTER_API_KEY_2 = _get("OPENROUTER_API_KEY_2")
OPENROUTER_API_KEY_LEGACY = _get("OPENROUTER_API_KEY")

# Eski va yangi environment nomlari bilan moslik.
OPENROUTER_API_KEYS = list(dict.fromkeys(
    key for key in (
        OPENROUTER_API_KEY_1,
        OPENROUTER_API_KEY_2,
        OPENROUTER_API_KEY_LEGACY,
    )
    if key
))

OPENROUTER_API_KEY = next(iter(OPENROUTER_API_KEYS), "")

OPENROUTER_MODEL = _get("OPENROUTER_MODEL", "openrouter/free")
OPENROUTER_VISION_MODEL = _get("OPENROUTER_VISION_MODEL")


# ================================================================
# AI PROVAYDERLARI
# ================================================================

# Presenton: kalit bo'sh bo'lsa, bot ichki PowerPoint generatoridan foydalanadi.
PRESENTON_API_KEY = _get("PRESENTON_API_KEY")
PRESENTON_BASE_URL = _get(
    "PRESENTON_BASE_URL", "https://api.presenton.ai"
).rstrip("/")
PRESENTON_TIMEOUT = max(10, min(_int("PRESENTON_TIMEOUT", 300), 600))
PRESENTON_ENABLED = bool(PRESENTON_API_KEY)

PROVIDERS = ("groq", "openrouter", "gemini")



def _provider_defaults(groq_key, openrouter_keys, gemini_key,
                       chat_preference="", file_preference=""):
    configured = []
    if groq_key:
        configured.append("groq")
    if openrouter_keys:
        configured.append("openrouter")
    if gemini_key:
        configured.append("gemini")
    if not configured:
        raise SystemExit(
            "AI provayderi sozlanmagan: GROQ_API_KEY, OPENROUTER_API_KEY_1/2 yoki GEMINI_API_KEY dan kamida bittasini kiriting."
        )

    for name, preference in (
        ("CHAT_PROVIDER", chat_preference),
        ("FILE_PROVIDER", file_preference),
    ):
        if preference and preference.lower() not in PROVIDERS:
            raise SystemExit(
                f"{name} noto'g'ri. Qiymat groq, openrouter yoki gemini bo'lishi mumkin."
            )

    chat_provider = (chat_preference or configured[0]).lower()
    if chat_provider not in configured:
        chat_provider = configured[0]
    file_provider = (
        file_preference
        or ("openrouter" if "openrouter" in configured else chat_provider)
    ).lower()
    if file_provider not in configured:
        file_provider = chat_provider
    return chat_provider, file_provider


CHAT_PROVIDER, FILE_PROVIDER = _provider_defaults(
    GROQ_API_KEY,
    OPENROUTER_API_KEYS,
    GEMINI_API_KEY,
    _get("CHAT_PROVIDER"),
    _get("FILE_PROVIDER"),
)


# ================================================================
# CLOUDFLARE — RASM YARATISH VA TAHRIRLASH
# ================================================================

CF_ACCOUNT_ID = _get("CF_ACCOUNT_ID")
CF_API_TOKEN = _get("CF_API_TOKEN")
IMAGES_ENABLED = bool(CF_ACCOUNT_ID and CF_API_TOKEN)

IMAGE_MODEL = _get(
    "IMAGE_MODEL",
    "@cf/black-forest-labs/flux-1-schnell",
)

EDIT_MODEL = _get(
    "EDIT_MODEL",
    "@cf/black-forest-labs/flux-2-klein-4b",
)


# ================================================================
# GROQ MODELLARI
# ================================================================

CHAT_MODEL = _get("CHAT_MODEL", "openai/gpt-oss-120b")
VISION_MODEL = _get("VISION_MODEL", "qwen/qwen3.8-27b")
CLASSIFIER_MODEL = _get("CLASSIFIER_MODEL", "openai/gpt-oss-20b")

STT_MODEL = _get("STT_MODEL", "whisper-large-v3-turbo")
STT_LANGUAGE = _get("STT_LANGUAGE") or None

REASONING_EFFORT = _get("REASONING_EFFORT", "low")
if REASONING_EFFORT not in ("low", "medium", "high"):
    REASONING_EFFORT = "low"


# ================================================================
# TOKEN LIMITLARI
# ================================================================

MAX_OUTPUT_TOKENS = 8192
CHAT_MAX_TOKENS = max(1, min(_int("CHAT_MAX_TOKENS", 2048), MAX_OUTPUT_TOKENS))

PRESENTATION_MAX_TOKENS = max(
    1,
    min(_int("PRESENTATION_MAX_TOKENS", 4096), MAX_OUTPUT_TOKENS),
)
FILE_MAX_TOKENS = max(1, min(_int("FILE_MAX_TOKENS", 2048), MAX_OUTPUT_TOKENS))

GROQ_CONTEXT_TOKENS = max(1024, _int("GROQ_CONTEXT_TOKENS", 32768))
OPENROUTER_CONTEXT_TOKENS = max(1024, _int("OPENROUTER_CONTEXT_TOKENS", 8192))
GEMINI_CONTEXT_TOKENS = max(1024, _int("GEMINI_CONTEXT_TOKENS", 32768))


# ================================================================
# RUXSATLAR VA KUNLIK LIMITLAR
# ================================================================

# Bo'sh bo'lsa, bot.py boshqacha cheklamagan taqdirda hamma foydalanuvchi
# foydalanishi mumkin. ID'lar vergul bilan ajratiladi.
ALLOWED_USERS = _id_set("ALLOWED_USERS")

IMAGE_DAILY_LIMIT = max(0, _int("IMAGE_DAILY_LIMIT", 5))
FILE_DAILY_LIMIT = max(0, _int("FILE_DAILY_LIMIT", 10))
RATE_LIMIT_SECONDS = max(0.0, _float("RATE_LIMIT_SECONDS", 3.0))
VOICE_MAX_SECONDS = max(1, _int("VOICE_MAX_SECONDS", 120))


# ================================================================
# SUHBAT XOTIRASI
# ================================================================

HISTORY_LIMIT = max(2, _int("HISTORY_LIMIT", 10))
HISTORY_LIMIT -= HISTORY_LIMIT % 2

HISTORY_MSG_CHARS = max(100, _int("HISTORY_MSG_CHARS", 1500))


# ================================================================
# KESHLAR
# ================================================================

IMAGE_CACHE = max(1, _int("IMAGE_CACHE", 100))
FILE_CACHE = max(1, _int("FILE_CACHE", 500))
DECK_CACHE = max(1, _int("DECK_CACHE", 100))


# ================================================================
# FAYL LIMITLARI
# ================================================================

FILE_MAX_BYTES = max(
    1024 * 1024,
    _int("FILE_MAX_BYTES", 20 * 1024 * 1024),
)

FILE_MAX_CHARS = max(
    1000,
    _int("FILE_MAX_CHARS", 20000),
)

XLSX_MAX_ROWS = max(100, _int("XLSX_MAX_ROWS", 5000))


# ================================================================
# TAQDIMOT SIFATI
# ================================================================

# 1 = bitta model, 2 = ikki model, 3 = uch model.
# Ko'p model ishlatish API sarfini va javob vaqtini oshirishi mumkin.
BEST_OF_N = max(1, min(_int("BEST_OF_N", 2), 3))


# ================================================================
# TEXNIK SOZLAMALAR
# ================================================================

WORKERS = max(1, min(_int("WORKERS", 8), 32))

REQUEST_TIMEOUT = max(
    5,
    min(_int("REQUEST_TIMEOUT", 60), 300),
)

IMAGE_TIMEOUT = max(
    5,
    min(_int("IMAGE_TIMEOUT", 120), 600),
)

TELEGRAM_MAX_CHARS = 3500

# Northflank PORT qiymatini avtomatik ishlatadi.
HEALTH_PORT = max(0, _int("PORT", _int("HEALTH_PORT", 8080)))
