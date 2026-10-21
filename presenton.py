"""Presenton API mijozi: taqdimot yaratish, tahrirlash va yuklab olish.

Bu modul Presenton API bilan ishlash uchun mo'ljallangan. U quyidagi funksiyalarni taqdim etadi:
- Yangi taqdimot yaratish (matn asosida)
- Mavjud taqdimotni tahrirlash
- Taqdimotni PPTX/PDF formatida yuklab olish
- Xatoliklarni boshqarish, qayta urinish va kesh
"""
import hashlib
import json
import logging
import time
from typing import Any

import requests

import config

log = logging.getLogger("presenton")

# ================================================================ Sozlamalar
PRESENTON_BASE = config.PRESENTON_BASE_URL.rstrip("/") if hasattr(config, "PRESENTON_BASE_URL") else "https://api.presenton.ai"
PRESENTON_API_KEY = getattr(config, "PRESENTON_API_KEY", "")
PRESENTON_TIMEOUT = getattr(config, "PRESENTON_TIMEOUT", 120)  # soniya
PRESENTON_RETRIES = getattr(config, "PRESENTON_RETRIES", 3)
PRESENTON_CACHE_TTL = getattr(config, "PRESENTON_CACHE_TTL", 3600)  # 1 soat

# Kesh (xotirada)
_CACHE: dict[str, tuple[float, Any]] = {}


class PresentonError(Exception):
    """Foydalanuvchiga ko'rsatish mumkin bo'lgan xato."""


# ================================================================ Yordamchi funksiyalar
def _cache_key(*args) -> str:
    """Argumentlardan unikal kesh kaliti yaratadi."""
    raw = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> Any | None:
    """Keshdan qiymat oladi (agar muddati o'tmagan bo'lsa)."""
    if key not in _CACHE:
        return None
    ts, value = _CACHE[key]
    if time.time() - ts > PRESENTON_CACHE_TTL:
        del _CACHE[key]
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    """Keshga qiymat yozadi."""
    _CACHE[key] = (time.time(), value)
    # Kesh hajmini cheklash
    if len(_CACHE) > 200:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        del _CACHE[oldest]


def _headers() -> dict[str, str]:
    """API so'rov uchun sarlavhalar."""
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if PRESENTON_API_KEY:
        h["Authorization"] = f"Bearer {PRESENTON_API_KEY}"
    return h


def _request(method: str, path: str, **kwargs) -> requests.Response:
    """API'ga so'rov yuboradi, qayta urinish va xatoliklarni boshqaradi."""
    url = f"{PRESENTON_BASE}{path}"
    last_error: Exception | None = None
    for attempt in range(1, PRESENTON_RETRIES + 1):
        try:
            resp = requests.request(
                method,
                url,
                headers=_headers(),
                timeout=PRESENTON_TIMEOUT,
                **kwargs,
            )
            if resp.status_code == 200:
                return resp
            # 4xx — qayta urinish mantiqiy emas
            if 400 <= resp.status_code < 500:
                if resp.status_code == 401:
                    raise PresentonError("Presenton API kaliti noto'g'ri.")
                if resp.status_code == 404:
                    raise PresentonError("Presenton API manzili topilmadi.")
                if resp.status_code == 429:
                    wait = int(resp.headers.get("retry-after", 5))
                    log.warning("Presenton: 429 limit, %s sekund kutish", wait)
                    time.sleep(min(wait, 30))
                    last_error = PresentonError("Presenton: so'rovlar limiti tugadi.")
                    continue
                # Boshqa 4xx
                raise PresentonError(f"Presenton so'rov xatosi ({resp.status_code}): {resp.text[:200]}")
            # 5xx — qayta urinish mumkin
            log.warning("Presenton: HTTP %s (urinish %s/%s)", resp.status_code, attempt, PRESENTON_RETRIES)
            last_error = PresentonError(f"Presenton serveri band ({resp.status_code}).")
            time.sleep(2 * attempt)
        except requests.RequestException as exc:
            log.warning("Presenton: tarmoq xatosi: %s", exc)
            last_error = PresentonError("Presenton serveriga ulanib bo'lmadi.")
            time.sleep(2 * attempt)
    raise last_error or PresentonError("Presenton: noma'lum xato.")


# ================================================================ Asosiy API
def create_presentation(
    prompt: str,
    n_slides: int = 8,
    theme: str = "midnight",
    language: str = "uz",
    export_as: str = "pptx",
) -> dict:
    """Yangi taqdimot yaratadi.

    Args:
        prompt: Taqdimot mavzusi/tavsifi.
        n_slides: Slaydlar soni (1-20).
        theme: Mavzu nomi (midnight, ocean, sunset, forest, royal, minimal).
        language: Til kodi (uz, ru, en).
        export_as: Eksport formati (pptx, pdf).

    Returns:
        dict: {"id": str, "url": str, "title": str, ...}

    Raises:
        PresentonError: Xatolik yuz berganda.
    """
    # Validatsiya
    if not prompt or not prompt.strip():
        raise PresentonError("Taqdimot mavzusi bo'sh bo'lishi mumkin emas.")
    n_slides = max(1, min(int(n_slides), 20))
    language = (language or "uz").lower()[:2]
    export_as = (export_as or "pptx").lower()
    if export_as not in ("pptx", "pdf"):
        export_as = "pptx"

    # Kesh tekshiruvi
    key = _cache_key("create", prompt, n_slides, theme, language, export_as)
    cached = _cache_get(key)
    if cached is not None:
        log.info("Presenton: keshdan qaytarildi")
        return cached

    payload = {
        "prompt": prompt.strip(),
        "n_slides": n_slides,
        "theme": theme,
        "language": language,
        "export_as": export_as,
    }
    log.info("Presenton: taqdimot yaratilmoqda (n_slides=%s, theme=%s)", n_slides, theme)
    resp = _request("POST", "/api/v1/presentation", json=payload)

    # Javobni tekshirish
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Presenton: JSON xato: %s", exc)
        raise PresentonError("Presenton noto'g'ri javob qaytardi.") from exc

    if not isinstance(data, dict) or "id" not in data:
        log.error("Presenton: javobda 'id' yo'q: %s", str(data)[:300])
        raise PresentonError("Presenton taqdimot ID'sini qaytarmadi.")

    _cache_set(key, data)
    return data


def get_presentation(presentation_id: str) -> dict:
    """Taqdimot holatini tekshiradi.

    Args:
        presentation_id: Taqdimot ID'si.

    Returns:
        dict: {"id": str, "status": str, "url": str, ...}
    """
    if not presentation_id:
        raise PresentonError("Taqdimot ID'si bo'sh.")
    resp = _request("GET", f"/api/v1/presentation/{presentation_id}")
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise PresentonError("Presenton noto'g'ri javob qaytardi.") from exc
    if not isinstance(data, dict):
        raise PresentonError("Presenton javobi dict emas.")
    return data


def wait_for_presentation(
    presentation_id: str,
    timeout: int = 300,
    poll_interval: float = 2.0,
) -> dict:
    """Taqdimot tayyor bo'lguncha kutadi.

    Args:
        presentation_id: Taqdimot ID'si.
        timeout: Maksimal kutish vaqti (soniya).
        poll_interval: Tekshirish orasidagi interval (soniya).

    Returns:
        dict: Tayyor taqdimot ma'lumotlari.
    """
    start = time.time()
    while time.time() - start < timeout:
        data = get_presentation(presentation_id)
        status = (data.get("status") or "").lower()
        if status in ("completed", "ready", "done", "success"):
            return data
        if status in ("failed", "error", "cancelled"):
            raise PresentonError(f"Taqdimot yaratishda xato: {data.get('error', 'noma\\'lum')}")
        time.sleep(poll_interval)
    raise PresentonError(f"Taqdimot {timeout} soniya ichida tayyor bo'lmadi.")


def download_presentation(presentation_id: str, export_as: str = "pptx") -> bytes:
    """Taqdimotni yuklab oladi.

    Args:
        presentation_id: Taqdimot ID'si.
        export_as: Format (pptx, pdf).

    Returns:
        bytes: Fayl mazmuni.
    """
    if not presentation_id:
        raise PresentonError("Taqdimot ID'si bo'sh.")
    export_as = (export_as or "pptx").lower()
    if export_as not in ("pptx", "pdf"):
        export_as = "pptx"
    path = f"/api/v1/presentation/{presentation_id}/download?format={export_as}"
    resp = _request("GET", path)
    if not resp.content:
        raise PresentonError("Presenton bo'sh fayl qaytardi.")
    return resp.content


def delete_presentation(presentation_id: str) -> bool:
    """Taqdimotni o'chiradi.

    Returns:
        bool: Muvaffaqiyatli bo'lsa True.
    """
    if not presentation_id:
        return False
    try:
        _request("DELETE", f"/api/v1/presentation/{presentation_id}")
        return True
    except PresentonError as exc:
        log.warning("Presenton: o'chirishda xato: %s", exc)
        return False


# ================================================================ Yuqori darajali funksiya
def generate_presentation(
    prompt: str,
    n_slides: int = 8,
    theme: str = "midnight",
    language: str = "uz",
    export_as: str = "pptx",
    wait_timeout: int = 300,
) -> tuple[str, bytes, str]:
    """Taqdimotni to'liq sikl: yaratish → kutish → yuklab olish.

    Returns:
        tuple: (fayl_nomi, fayl_mazmuni, xulosa)
    """
    # 1. Yaratish
    created = create_presentation(
        prompt=prompt,
        n_slides=n_slides,
        theme=theme,
        language=language,
        export_as=export_as,
    )
    pid = created.get("id")
    if not pid:
        raise PresentonError("Taqdimot ID'si olinmadi.")

    # 2. Kutish
    try:
        final = wait_for_presentation(pid, timeout=wait_timeout)
    except PresentonError:
        # Xato bo'lsa, tozalash
        delete_presentation(pid)
        raise

    # 3. Yuklab olish
    data = download_presentation(pid, export_as=export_as)

    # 4. Nom va xulosa
    title = final.get("title") or prompt[:60]
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip() or "taqdimot"
    ext = "pptx" if export_as == "pptx" else "pdf"
    filename = f"{safe_title}.{ext}"
    summary = f"Taqdimot tayyor: {n_slides} slayd, mavzu «{title}»"

    # 5. Tozalash (ixtiyoriy)
    try:
        delete_presentation(pid)
    except Exception:  # noqa: BLE001
        pass

    return filename, data, summary
