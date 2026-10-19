"""Rasm bilan ishlash: Cloudflare Workers AI orqali yaratish/tahrirlash va Pillow bilan kichraytirish."""
import base64
import io
import logging
import time

import requests
from PIL import Image, ImageOps

import config

log = logging.getLogger("images")


class ImageError(Exception):
    """Foydalanuvchiga ko'rsatish mumkin bo'lgan xato."""


def shrink(data: bytes, max_side: int = 1280, quality: int = 85) -> bytes:
    """Rasmni JPEG ga o'giradi va kattaligini cheklaydi (Groq/Cloudflare limitlari va xotira uchun)."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _cf_url(model: str) -> str:
    return f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/ai/run/{model}"


def _pick_image(resp: requests.Response) -> bytes:
    if resp.headers.get("content-type", "").startswith("image/"):
        return resp.content
    data = resp.json()
    b64 = (data.get("result") or {}).get("image") or data.get("image")
    if not b64:
        raise KeyError("image topilmadi")
    return base64.b64decode(b64)


def _cf_post(model: str, what: str, **kwargs) -> bytes:
    if not config.IMAGES_ENABLED:
        raise ImageError("Rasm funksiyasi sozlanmagan.")
    for attempt in range(2):
        try:
            resp = requests.post(
                _cf_url(model),
                headers={"Authorization": "Bearer " + config.CF_API_TOKEN},
                timeout=config.IMAGE_TIMEOUT,
                **kwargs,
            )
        except requests.RequestException as exc:
            log.warning("Cloudflare (%s) tarmoq xatosi: %s", what, exc)
            if attempt == 0:
                time.sleep(2)
                continue
            raise ImageError("Rasm serveriga ulanib bo'lmadi, qayta urinib ko'ring.")

        if resp.status_code == 200:
            try:
                return _pick_image(resp)
            except Exception:  # noqa: BLE001
                log.warning("Cloudflare (%s) kutilmagan javob: %s", what, resp.text[:300])
                raise ImageError("Rasm modeli noto'g'ri javob qaytardi.")

        log.warning("Cloudflare (%s) HTTP %s: %s", what, resp.status_code, resp.text[:300])
        if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
            time.sleep(3)
            continue
        if resp.status_code == 429:
            raise ImageError("Rasm limiti tugadi, birozdan keyin urinib ko'ring.")
        if resp.status_code in (401, 403):
            raise ImageError("Cloudflare kaliti noto'g'ri yoki ruxsat yo'q.")
        raise ImageError("Rasm yaratib bo'lmadi, qayta urinib ko'ring.")
    raise ImageError("Rasm yaratib bo'lmadi, qayta urinib ko'ring.")


def make_image(prompt: str) -> bytes:
    return _cf_post(config.IMAGE_MODEL, "chizish", json={"prompt": prompt})


def edit_image(img_bytes: bytes, prompt: str) -> bytes:
    return _cf_post(
        config.EDIT_MODEL,
        "tahrir",
        files={
            "prompt": (None, prompt),
            "input_image_0": ("image.jpg", img_bytes, "image/jpeg"),
        },
    )
