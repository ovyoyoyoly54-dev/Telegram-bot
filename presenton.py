"""Presenton (ochiq kodli Gamma o'rnini bosuvchi) mijozi: taqdimot (PPTX/PDF) yaratadi.
Presenton alohida servis sifatida ishlaydi (ghcr.io/presenton/presenton), bot unga HTTP orqali murojaat qiladi."""
import logging
import os

import requests

import config

log = logging.getLogger("presenton")

MAX_DOWNLOAD = 45 * 1024 * 1024  # Telegram'ga yuborish chegarasidan kichikroq


class PresentonError(Exception):
    """Foydalanuvchiga ko'rsatish mumkin bo'lgan xato."""


def _headers() -> dict:
    return {"Authorization": "Bearer " + config.PRESENTON_API_KEY} if config.PRESENTON_API_KEY else {}


def _full(base: str, path: str) -> str:
    if path.startswith("http"):
        return path
    return base + (path if path.startswith("/") else "/" + path)


def generate(content: str, export_as: str = "pptx") -> tuple[str, bytes]:
    """Taqdimot yaratadi. (fayl_nomi, baytlar) qaytaradi."""
    base = config.PRESENTON_URL.rstrip("/")
    body = {
        "content": content[:4000],
        "language": config.PRESENTON_LANGUAGE,
        "template": config.PRESENTON_TEMPLATE,
        "export_as": export_as if export_as in ("pptx", "pdf") else "pptx",
    }
    if config.PRESENTON_SLIDES > 0:
        body["n_slides"] = config.PRESENTON_SLIDES
    try:
        resp = requests.post(base + "/api/v1/ppt/presentation/generate", json=body,
                             headers=_headers(), timeout=config.PRESENTON_TIMEOUT)
    except requests.RequestException as exc:
        log.warning("Presenton tarmoq xatosi: %s", exc)
        raise PresentonError("Presenton serveriga ulanib bo'lmadi yoki javob juda kech keldi.") from exc
    if resp.status_code in (401, 403):
        raise PresentonError("Presenton API kaliti noto'g'ri yoki ruxsat yo'q.")
    if resp.status_code != 200:
        log.warning("Presenton HTTP %s: %s", resp.status_code, resp.text[:300])
        raise PresentonError("Taqdimot yaratib bo'lmadi (Presenton xato qaytardi).")
    try:
        path = str(resp.json().get("path") or "")
    except ValueError:
        path = ""
    if not path:
        log.warning("Presenton javobida 'path' yo'q: %s", resp.text[:300])
        raise PresentonError("Presenton fayl manzilini qaytarmadi.")

    url = _full(base, path)
    try:
        dl = requests.get(url, headers=_headers(), timeout=120)
    except requests.RequestException as exc:
        log.warning("Presenton faylni yuklashda xato: %s", exc)
        raise PresentonError("Tayyor taqdimotni yuklab bo'lmadi.") from exc
    if dl.status_code != 200 or not dl.content:
        log.warning("Presenton yuklash HTTP %s: %s", dl.status_code, url)
        raise PresentonError("Tayyor taqdimotni yuklab bo'lmadi.")
    if len(dl.content) > MAX_DOWNLOAD:
        raise PresentonError("Taqdimot fayli Telegram uchun juda katta.")
    name = os.path.basename(path.split("?")[0]) or f"taqdimot.{body['export_as']}"
    return name, dl.content
