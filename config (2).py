"""Sozlamalar: hammasi muhit o'zgaruvchilaridan (Northflank) yoki lokal .env dan o'qiladi."""
import os

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return default


def _required(name: str) -> str:
    value = _get(name)
    if not value:
        raise SystemExit(f"❌ {name} topilmadi. Muhit o'zgaruvchilarini (Environment variables) tekshiring.")
    return value


def _id_set(name: str) -> set[int]:
    """Vergul bilan ajratilgan Telegram ID'lar. Noto'g'ri qiymat bo'lsa, bot ISHGA TUSHMAYDI
    (aks holda cheklov jimgina o'chib, bot hammaga ochiq bo'lib qolardi)."""
    raw = _get(name)
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.lstrip("-").isdigit():
            raise SystemExit(f"❌ {name} noto'g'ri: '{part}'. Faqat raqamli Telegram ID, vergul bilan ajrating.")
        ids.add(int(part))
    return ids


# --- Majburiy kalitlar ---
TELEGRAM_TOKEN = _required("TELEGRAM_TOKEN")
GROQ_API_KEY = _required("GROQ_API_KEY")

# --- Ixtiyoriy: Gemini (Groq ishlamay qolsa, zaxira sifatida) ---
GEMINI_API_KEY = _get("GEMINI_API_KEY")
GEMINI_MODEL = _get("GEMINI_MODEL", "gemini-2.5-flash")

# --- Ixtiyoriy: Cloudflare (rasm chizish va tahrirlash) ---
CF_ACCOUNT_ID = _get("CF_ACCOUNT_ID")
CF_API_TOKEN = _get("CF_API_TOKEN")
IMAGES_ENABLED = bool(CF_ACCOUNT_ID and CF_API_TOKEN)
IMAGE_MODEL = _get("IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
EDIT_MODEL = _get("EDIT_MODEL", "@cf/black-forest-labs/flux-2-klein-4b")

# --- Groq modellari (kerak bo'lsa env orqali almashtiring) ---
CHAT_MODEL = _get("CHAT_MODEL", "openai/gpt-oss-120b")
VISION_MODEL = _get("VISION_MODEL", "qwen/qwen3.8-27b")
CLASSIFIER_MODEL = _get("CLASSIFIER_MODEL", "openai/gpt-oss-20b")
STT_MODEL = _get("STT_MODEL", "whisper-large-v3-turbo")
STT_LANGUAGE = _get("STT_LANGUAGE")  # masalan "uz"; bo'sh bo'lsa avtomatik aniqlanadi
REASONING_EFFORT = _get("REASONING_EFFORT", "low")  # faqat gpt-oss uchun: low | medium | high
CHAT_MAX_TOKENS = _int("CHAT_MAX_TOKENS", 2048)

# --- Kirish nazorati va limitlar ---
ALLOWED_USERS = _id_set("ALLOWED_USERS")      # bo'sh bo'lsa — hamma foydalanishi mumkin
IMAGE_DAILY_LIMIT = _int("IMAGE_DAILY_LIMIT", 5)  # bir foydalanuvchiga kuniga; 0 = cheksiz
RATE_LIMIT_SECONDS = _float("RATE_LIMIT_SECONDS", 3.0)
VOICE_MAX_SECONDS = _int("VOICE_MAX_SECONDS", 120)

# --- Xotira ---
HISTORY_LIMIT = max(2, _int("HISTORY_LIMIT", 10))
HISTORY_LIMIT -= HISTORY_LIMIT % 2   # juft bo'lsin: (savol, javob) juftliklari buzilmasin
HISTORY_MSG_CHARS = _int("HISTORY_MSG_CHARS", 1500)  # tarixda har xabardan saqlanadigan max belgi
IMAGE_CACHE = _int("IMAGE_CACHE", 100)               # nechta foydalanuvchining oxirgi rasmi saqlanadi

# --- Texnik ---
WORKERS = _int("WORKERS", 8)                 # bir vaqtda ishlov beriladigan xabarlar soni
REQUEST_TIMEOUT = _int("REQUEST_TIMEOUT", 60)
IMAGE_TIMEOUT = _int("IMAGE_TIMEOUT", 120)
TELEGRAM_MAX_CHARS = 3500                    # Telegram limiti 4096; HTML belgilar uchun zaxira
HEALTH_PORT = _int("PORT", _int("HEALTH_PORT", 8080))  # 0 = health server o'chiq
