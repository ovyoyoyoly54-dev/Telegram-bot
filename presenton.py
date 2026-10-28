"""Presenton cloud API client for generating downloadable presentations."""

import logging
import re
from urllib.parse import urljoin, urlparse

import requests

import config

log = logging.getLogger("presenton")


class PresentonError(Exception):
    """Safe, user-facing Presenton service error."""


def _headers() -> dict[str, str]:
    if not config.PRESENTON_API_KEY:
        raise PresentonError("Presenton xizmati sozlanmagan.")
    return {
        "Authorization": f"Bearer {config.PRESENTON_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def generate_presentation(
    prompt: str,
    n_slides: int = 8,
    theme: str = "general",
    language: str = "Uzbek",
    export_as: str = "pptx",
) -> tuple[str, bytes, str]:
    """Create a presentation with Presenton's synchronous API and download it."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise PresentonError("Taqdimot mavzusi bo'sh bo'lishi mumkin emas.")
    export_as = "pptx"
    detailed_content = (
        f"Foydalanuvchi mavzusi: {prompt}\n\n"
        "Shu mavzuni boshidan oxirigacha batafsil va tushunarli yoritadigan "
        "taqdimot yarating. Kirish, mantiqiy ketma-ketlikdagi asosiy bo'limlar "
        "va aniq xulosani kiriting. Har bir mazmuniy slaydda sarlavhadan tashqari "
        "tushuntirish, sabab-oqibat yoki amaliy qo'llanish bo'lsin. Kamida ikki "
        "konkret misol keltirib, nima uchun ular mavzuga mosligini izohlang. "
        "O'zbek tilida tabiiy va grammatik jihatdan to'g'ri yozing; ega-kesimni, "
        "kelishik va qo'shimchalarni moslang. Bir atamani taqdimot bo'ylab bir xil "
        "ishlating va har bir gapning mazmuniy bog'lanishini tekshiring. "
        "Slaydlar faqat sarlavha yoki bezak rasmlar bilan cheklanmasin. "
        "Har bir slaydga mavzuga xos, turli kompozitsiyali sifatli tasvir qo'shing; "
        "takroriy fon va bir xil sahna ishlatmang. Mavzuga aloqasiz yoki "
        "tekshirilmagan faktlarni qo'shmang."
    )

    payload = {
        "content": detailed_content,
        "n_slides": max(5, min(int(n_slides), 20)),
        "language": "Uzbek",
        "standard_template": "general",
        "export_as": export_as,
    }
    base_url = config.PRESENTON_BASE_URL.rstrip("/") + "/"
    try:
        response = requests.post(
            urljoin(base_url, "api/v3/presentation/generate"),
            headers=_headers(), json=payload, timeout=config.PRESENTON_TIMEOUT,
        )
        if response.status_code >= 400:
            log.warning("Presenton generation failed with HTTP %s", response.status_code)
            if response.status_code in (401, 403):
                raise PresentonError("Presenton API kaliti noto'g'ri yoki ruxsatsiz.")
            if response.status_code == 429:
                raise PresentonError("Presenton so'rovlar limiti tugadi.")
            raise PresentonError(f"Presenton taqdimotni yarata olmadi (HTTP {response.status_code}).")
        result = response.json()
        if not isinstance(result, dict):
            raise PresentonError("Presenton noto'g'ri javob qaytardi.")
        file_path = result.get("path")
        if not isinstance(file_path, str) or not file_path.strip():
            raise PresentonError("Presenton fayl manzilini qaytarmadi.")

        download_url = urljoin(base_url, file_path)
        file_parts = urlparse(download_url)
        base_parts = urlparse(base_url)
        if (
            file_parts.scheme != "https"
            or file_parts.hostname != base_parts.hostname
            or file_parts.port != base_parts.port
            or file_parts.username
            or file_parts.password
        ):
            raise PresentonError("Presenton xavfsiz fayl manzilini qaytarmadi.")
        file_response = requests.get(
            download_url, headers={"Authorization": f"Bearer {config.PRESENTON_API_KEY}"},
            timeout=config.PRESENTON_TIMEOUT,
        )
        file_response.raise_for_status()
        data = file_response.content
    except requests.Timeout as exc:
        raise PresentonError("Presenton javobi kutilgan vaqtda kelmadi.") from exc
    except requests.RequestException as exc:
        log.warning("Presenton network/download error: %s", type(exc).__name__)
        raise PresentonError("Presenton xizmatiga ulanib bo'lmadi.") from exc
    except ValueError as exc:
        raise PresentonError("Presenton noto'g'ri javob qaytardi.") from exc

    if not data:
        raise PresentonError("Presenton bo'sh fayl qaytardi.")

    title = str(result.get("title") or prompt.splitlines()[0])[:100]
    safe_title = re.sub(r"[^\w -]", "", title, flags=re.UNICODE).strip() or "taqdimot"
    filename = f"{safe_title}.{export_as}"
    summary = f"Presenton orqali tayyorlandi: {payload['n_slides']} slayd, «{title}»."
    return filename, data, summary
