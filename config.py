"""Telegram bot sozlamalari.

Barcha qiymatlar .env fayli yoki Environment Variables orqali o'rnatiladi.
Agar biror qiymat berilmasa — default ishlatiladi.
"""
import os

from dotenv import load_dotenv

load_dotenv()


# ================================================================
# Yordamchi funksiyalar
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
            raise SystemExit(f"❌ {name} noto'g'ri: {item}")
        result.add(int(item))
    return result


# ================================================================
# Majburiy API kalitlari
# ================================================================

TELEGRAM_TOKEN = _required("TELEGRAM_TOKEN")
GROQ_API_KEY = _required("GROQ_API_KEY")


# ================================================================
# AI provayderlari
# ================================================================

# Gemini (zaxira)
GEMINI_API_KEY = _get("GEMINI_API_KEY")
GEMINI_MODEL = _get("GEMINI_MODEL", "gemini-2.5-flash")

# OpenRouter (zaxira)
OPENROUTER_API_KEY = _get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = _get("OPENROUTER_MODEL", "openrouter/free")
OPENROUTER_VISION_MODEL = _get("OPENROUTER_VISION_MODEL") or OPENROUTER_MODEL

PROVIDERS = ("groq", "openrouter", "gemini")

# Asosiy provayderlar (fallback zanjiri shu tartibda ishlaydi)
CHAT_PROVIDER = _get("CHAT_PROVIDER", "groq").lower()
FILE_PROVIDER = _get("FILE_PROVIDER", "openrouter" if OPENROUTER_API_KEY else "groq").lower()

# Tekshirish: provayder nomi to'g'rimi va API kaliti mavjudmi
for _name, _value in (("CHAT_PROVIDER", CHAT_PROVIDER), ("FILE_PROVIDER", FILE_PROVIDER)):
    if _value not in PROVIDERS:
        raise SystemExit(f"❌ {_name} noto'g'ri. Qiymat: groq, openrouter yoki gemini.")
    if _value == "groq" and not GROQ_API_KEY:
        raise SystemExit(f"❌ {_name}=groq, lekin GROQ_API_KEY topilmadi.")
    if _value == "openrouter" and not OPENROUTER_API_KEY:
        raise SystemExit(f"❌ {_name}=openrouter, lekin OPENROUTER_API_KEY topilmadi.")
    if _value == "gemini" and not GEMINI_API_KEY:
        raise SystemExit(f"❌ {_name}=gemini, lekin GEMINI_API_KEY topilmadi.")


# ================================================================
# Cloudflare (rasm yaratish/tahrirlash)
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
# Groq modellari
# ================================================================

CHAT_MODEL = _get("CHAT_MODEL", "openai/gpt-oss-120b")
VISION_MODEL = _get("VISION_MODEL", "qwen/qwen3.8-27b")
CLASSIFIER_MODEL = _get("CLASSIFIER_MODEL", "openai/gpt-oss-20b")
STT_MODEL = _get("STT_MODEL", "whisper-large-v3-turbo")
STT_LANGUAGE = _get("STT_LANGUAGE") or None  # bo'sh bo'lsa None (avto-aniqlash)
REASONING_EFFORT = _get("REASONING_EFFORT", "low")  # low | medium | high


# ================================================================
# Token limitlari
# ================================================================

# Oddiy suhbat uchun (yuqori chegara 16384)
CHAT_MAX_TOKENS = max(128, min(_int("CHAT_MAX_TOKENS", 8192), 16384))

# Taqdimot / fayl yaratish uchun (8 slaydli JSON sig'ishi shart)
PRESENTATION_MAX_TOKENS = max(4096, min(_int("PRESENTATION_MAX_TOKENS", 8192), 16384))


# ================================================================
# Ruxsatlar va limitlar
# ================================================================

# Bo'sh bo'lsa — hamma uchun ochiq; to'ldirilsa — faqat shu ID'lar
ALLOWED_USERS = _id_set("ALLOWED_USERS")

# Kunlik limitlar (0 = cheksiz)
IMAGE_DAILY_LIMIT = max(0, _int("IMAGE_DAILY_LIMIT", 5))
FILE_DAILY_LIMIT = max(0, _int("FILE_DAILY_LIMIT", 10))

# Tezlik limiti (soniya)
RATE_LIMIT_SECONDS = max(0.0, _float("RATE_LIMIT_SECONDS", 3.0))

# Ovozli xabar maksimal davomiyligi (soniya)
VOICE_MAX_SECONDS = max(1, _int("VOICE_MAX_SECONDS", 120))


# ================================================================
# Xotira (suhbat tarixi)
# ================================================================

# Nechta xabar eslab qolinadi (juft bo'lishi shart: user+assistant)
HISTORY_LIMIT = max(2, _int("HISTORY_LIMIT", 10))
HISTORY_LIMIT -= HISTORY_LIMIT % 2  # toq bo'lsa, juftga keltirish

# Bitta xabar maksimal uzunligi (belgi)
HISTORY_MSG_CHARS = max(100, _int("HISTORY_MSG_CHARS", 1500))


# ================================================================
# Keshlar (state.py ishlatadi)
# ================================================================

# Nechta foydalanuvchining oxirgi rasmi saqlanadi
IMAGE_CACHE = max(1, _int("IMAGE_CACHE", 100))

# Nechta foydalanuvchining yuklangan fayli saqlanadi
FILE_CACHE = max(1, _int("FILE_CACHE", 500))

# Nechta foydalanuvchining taqdimoti saqlanadi
DECK_CACHE = max(1, _int("DECK_CACHE", 100))


# ================================================================
# Fayl limitlari (docs.py va files.py ishlatadi)
# ================================================================

# Yuklanadigan fayl maksimal hajmi (bayt). Default: 20 MB
FILE_MAX_BYTES = max(
    1024 * 1024,
    _int("FILE_MAX_BYTES", 20 * 1024 * 1024),
)

# Fayldan o'qiladigan matn maksimal uzunligi (belgi). Default: 20 000
FILE_MAX_CHARS = max(
    1000,
    _int("FILE_MAX_CHARS", 20000),
)

# Excel faylida maksimal qatorlar soni (juda katta jadvalni cheklash uchun)
XLSX_MAX_ROWS = max(100, _int("XLSX_MAX_ROWS", 5000))


# ================================================================
# Taqdimot best-of-N (docs.py ishlatadi)
# ================================================================

# Taqdimot yaratishda nechta modeldan sinab, eng yaxshisini tanlash.
# 1 = oddiy llm.chat (tez, arzon)
# 2 = 2 ta model (tavsiya etiladi)
# 3 = 3 ta model (sekinroq, qimmatroq, lekin eng sifatli)
BEST_OF_N = max(1, min(_int("BEST_OF_N", 2), 3))


# ================================================================
# Texnik sozlamalar
# ================================================================

# Telegram bot workerlari soni (1-32)
WORKERS = max(1, min(_int("WORKERS", 8), 32))

# Umumiy HTTP so'rov timeout (soniya). 5-300
REQUEST_TIMEOUT = max(5, min(_int("REQUEST_TIMEOUT", 60), 300))

# Rasm yaratish timeout (soniya). 5-600
IMAGE_TIMEOUT = max(5, min(_int("IMAGE_TIMEOUT", 120), 600))

# Telegram xabar maksimal uzunligi (belgi). Telegram limiti: 4096
TELEGRAM_MAX_CHARS = 3500

# Health-check server porti (Northflank uchun). 0 = o'chirilgan
HEALTH_PORT = max(0, _int("PORT", _int("HEALTH_PORT", 8080)))