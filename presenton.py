"""Presenton Cloud and self-hosted presentation API client."""

import logging
import re
from urllib.parse import urljoin, urlparse

import requests

import config

log = logging.getLogger("presenton")


class PresentonError(Exception):
    """Safe service error with diagnostic fields that never contain credentials."""

    def __init__(self, message: str, *, kind: str = "PresentonError", status: int | None = None, detail: str = "", endpoint: str = ""):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.detail = detail
        self.endpoint = endpoint


def _base_url() -> str:
    raw = (config.PRESENTON_BASE_URL or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise PresentonError("Presenton base URL noto'g'ri.", kind="InvalidURL", detail="PRESENTON_BASE_URL must be an http(s) URL without embedded credentials")
    return raw.rstrip("/") + "/"


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if config.PRESENTON_API_MODE == "cloud":
        if not config.PRESENTON_API_KEY:
            raise PresentonError("Presenton xizmati sozlanmagan.", kind="ConfigurationError", detail="Cloud mode requires PRESENTON_API_KEY")
        headers["Authorization"] = f"Bearer {config.PRESENTON_API_KEY}"
    return headers


def _response_detail(response, prompt: str) -> str:
    """Extract service diagnostics while scrubbing credentials and request content."""
    try:
        body = response.json()
        if isinstance(body, dict):
            candidates = [body.get(key) for key in (
                "detail", "message", "error", "code", "reason", "status", "traceback",
            )]
            detail = "; ".join(str(value) for value in candidates if value)
            if not detail:
                detail = str(body)
        else:
            detail = str(body)
    except (ValueError, AttributeError):
        detail = getattr(response, "text", "") or ""
    detail = re.sub(r"(?i)bearer\s+\S+|sk-presenton-[\w-]+", "[REDACTED]", detail)
    if config.PRESENTON_API_KEY:
        detail = detail.replace(config.PRESENTON_API_KEY, "[REDACTED]")
    if prompt:
        detail = detail.replace(prompt, "[PROMPT REDACTED]")
    detail = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL REDACTED]", detail)
    detail = re.sub(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)", "[PHONE REDACTED]", detail)
    detail = re.sub(r"https?://[^\s\"']+", "[URL REDACTED]", detail)
    detail = re.sub(r"[\r\n\t]+", " ", detail)
    return detail[:1000].strip()


def _raise_http(response, prompt: str, operation: str, endpoint: str) -> None:
    status = response.status_code
    detail = _response_detail(response, prompt)
    if status in (401, 403):
        message, kind = "Presenton API kaliti noto'g'ri yoki ruxsatsiz.", "AuthenticationError"
    elif status == 404:
        message, kind = "Presenton endpoint yoki fayl topilmadi.", "EndpointNotFound"
    elif status == 422:
        message, kind = "Presenton so'rov formatini qabul qilmadi.", "ValidationError"
    elif status == 429:
        message, kind = "Presenton so'rovlar limiti tugadi.", "RateLimitError"
    elif 500 <= status <= 599:
        message, kind = "Presenton serverida xatolik yuz berdi.", "ServerError"
    else:
        message, kind = f"Presenton {operation} so'rovi bajarilmadi (HTTP {status}).", "HTTPError"
    raise PresentonError(message, kind=kind, status=status, detail=detail, endpoint=endpoint)


def generate_presentation(prompt: str, n_slides: int = 8, theme: str = "general", language: str = "Uzbek", export_as: str = "pptx") -> tuple[str, bytes, str]:
    """Create a PPTX using the configured Presenton deployment."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise PresentonError("Taqdimot mavzusi bo'sh bo'lishi mumkin emas.", kind="InputError")
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
    slides = max(5, min(int(n_slides), 20))
    cloud = config.PRESENTON_API_MODE == "cloud"
    payload = {"content": detailed_content, "n_slides": slides, "language": language,
               ("standard_template" if cloud else "template"): theme, "export_as": export_as}
    try:
        base_url = _base_url()
        headers = _headers()
        route = "api/v3/presentation/generate" if cloud else "api/v1/ppt/presentation/generate"
        endpoint = urljoin(base_url, route)
        safe_endpoint = f"{urlparse(endpoint).scheme}://{urlparse(endpoint).netloc}{urlparse(endpoint).path}"
        log.info("Presenton request: mode=%s endpoint=%s auth=%s fields=%s",
                 config.PRESENTON_API_MODE, safe_endpoint,
                 "bearer" if cloud else "none", ",".join(sorted(payload)))
        response = requests.post(endpoint, headers=headers, json=payload, timeout=config.PRESENTON_TIMEOUT)
        if response.status_code >= 400:
            _raise_http(response, prompt, "generation", safe_endpoint)
        try:
            result = response.json()
        except ValueError as exc:
            raise PresentonError("Presenton noto'g'ri javob qaytardi.", kind="InvalidResponse", detail="Response was not valid JSON") from exc
        if not isinstance(result, dict):
            raise PresentonError("Presenton noto'g'ri javob qaytardi.", kind="InvalidResponse", detail="Expected a JSON object")
        file_path = result.get("path")
        if not isinstance(file_path, str) or not file_path.strip():
            raise PresentonError("Presenton fayl manzilini qaytarmadi.", kind="InvalidResponse", detail="Missing path in successful response")
        download_url = urljoin(base_url, file_path)
        parts, base = urlparse(download_url), urlparse(base_url)
        if (parts.scheme != base.scheme or parts.hostname != base.hostname or parts.port != base.port
                or parts.username or parts.password):
            raise PresentonError("Presenton xavfsiz fayl manzilini qaytarmadi.", kind="InvalidDownloadURL", detail="Returned file URL is outside PRESENTON_BASE_URL origin")
        download_headers = {"Accept": "application/octet-stream"}
        if cloud:
            download_headers["Authorization"] = headers["Authorization"]
        file_response = requests.get(download_url, headers=download_headers, timeout=config.PRESENTON_TIMEOUT)
        if file_response.status_code >= 400:
            _raise_http(file_response, prompt, "download",
                        f"{parts.scheme}://{parts.netloc}/[download]")
        data = file_response.content
    except requests.Timeout as exc:
        raise PresentonError("Presenton javobi kutilgan vaqtda kelmadi.", kind="Timeout", detail=type(exc).__name__) from exc
    except requests.exceptions.InvalidURL as exc:
        raise PresentonError("Presenton URL noto'g'ri.", kind="InvalidURL", detail="Requests rejected the configured URL") from exc
    except requests.RequestException as exc:
        raise PresentonError("Presenton xizmatiga ulanib bo'lmadi.", kind="NetworkError", detail=type(exc).__name__) from exc

    if not data:
        raise PresentonError("Presenton bo'sh fayl qaytardi.", kind="InvalidFile", detail="Downloaded file was empty")
    title = str(result.get("title") or prompt.splitlines()[0])[:100]
    safe_title = re.sub(r"[^\w -]", "", title, flags=re.UNICODE).strip() or "taqdimot"
    return f"{safe_title}.{export_as}", data, f"Presenton orqali tayyorlandi: {slides} slayd, «{title}» .".replace("» .", "».")
