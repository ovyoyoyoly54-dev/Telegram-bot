"""LLM mijozi: Groq (asosiy) + Gemini (zaxira). Chat, rasm tushunish, ovoz → matn, tasniflash."""
import base64
import json
import logging
import re
import time

import requests

import config

log = logging.getLogger("llm")

GROQ_BASE = "https://api.groq.com/openai/v1"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_session = requests.Session()
_THINK = re.compile(r"<think>.*?</think>", re.S)

CLASSIFIER_PROMPT = (
    "Sen xabarni tasniflaysan. Faqat JSON qaytar: "
    '{"action": "chat" | "draw" | "edit" | "file", "prompt": "..."}. '
    "draw: foydalanuvchi tavsif asosida YANGI rasm chizishni/yaratishni so'raydi. "
    "edit: foydalanuvchi MAVJUD rasmni o'zgartirishni so'raydi "
    "(fon, rang, ob'ekt qo'shish/olib tashlash, uslub, qora-oq va hokazo); "
    "faqat has_image true bo'lsa edit tanla. "
    "file: foydalanuvchi FAYL yaratishni (hujjat, jadval, taqdimot/prezentatsiya/slayd, Word, Excel, PowerPoint, "
    "PDF, TXT, CSV, kod fayli va hokazo) yoki yuborilgan faylni (has_doc true) tahrirlash, tarjima qilish, "
    "boshqa formatga o'tkazish, to'ldirishni so'raydi. "
    "MUHIM: agar has_doc true bo'lsa va xabar mavjud taqdimot/slayd/hujjatga rasm qo'shish yoki uni o'zgartirish haqida bo'lsa, "
    "bu draw emas, file. "
    "chat: qolgan hamma narsa, shu jumladan rasm yoki fayl haqidagi savollar va xulosa so'rovlari "
    "('bu nima?', 'tushuntir', 'xulosa qil'). "
    "prompt: faqat draw/edit uchun — rasm modeli uchun qisqa INGLIZCHA ko'rsatma (draw uchun natijaning "
    "tavsifi, edit uchun qilinadigan o'zgarish). chat va file bo'lsa bo'sh qoldir."
)
REFINER_PROMPT = (
    "Foydalanuvchining rasm tavsifini rasm yaratish modeli uchun qisqa, aniq INGLIZCHA promptga "
    "aylantir. Faqat promptning o'zini yoz, boshqa hech narsa yozma."
)


class LLMError(Exception):
    """Foydalanuvchiga ko'rsatish mumkin bo'lgan xato."""


# ---------------------------------------------------------------- umumiy so'rov

def _retry_after(resp: requests.Response, default: float) -> float:
    try:
        return float(resp.headers.get("retry-after", default))
    except (TypeError, ValueError):
        return default


def _call(name: str, send, attempts: int = 3) -> requests.Response:
    """send() ni chaqiradi; 429/5xx/tarmoq xatolarida qayta urinadi, qolganini LLMError qiladi."""
    last = LLMError(f"{name}: noma'lum xato.")
    for i in range(attempts):
        try:
            resp = send()
        except requests.RequestException as exc:
            log.warning("%s tarmoq xatosi: %s", name, exc)
            last = LLMError(f"{name} serveriga ulanib bo'lmadi.")
            time.sleep(1.5 * (i + 1))
            continue

        code = resp.status_code
        if code == 200:
            return resp
        log.warning("%s HTTP %s: %s", name, code, resp.text[:300])

        if code in (401, 403):
            raise LLMError(f"{name}: API kalit noto'g'ri yoki ruxsat yo'q.")
        if code == 402:
            raise LLMError(f"{name}: hisobda mablag' yetarli emas (kreditni tekshiring).")
        if code == 404:
            raise LLMError(f"{name}: model topilmadi. Model nomini (env) tekshiring.")
        if code == 413:
            raise LLMError("So'rov juda katta (rasm yoki tarix). /clear qiling yoki kichikroq rasm yuboring.")
        if code == 400:
            if "API key" in resp.text:
                raise LLMError(f"{name}: API kalit noto'g'ri.")
            raise LLMError(f"{name} so'rovni qabul qilmadi (400). Model sozlamalarini tekshiring.")
        if code == 429:
            wait = _retry_after(resp, 3 * (i + 1))
            last = LLMError("So'rovlar limiti tugadi, birozdan keyin urinib ko'ring.")
            if wait <= 8 and i < attempts - 1:
                time.sleep(wait)
                continue
            raise last  # uzoq kutish kerak — oqimni band qilmaymiz
        if code >= 500:
            last = LLMError(f"{name} serveri band, birozdan keyin urinib ko'ring.")
            time.sleep(2 * (i + 1))
            continue
        raise LLMError(f"{name}: kutilmagan xato (kod {code}).")
    raise last


def _clean(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def _apply_reasoning(payload: dict) -> None:
    """gpt-oss modellarida 'o'ylash' tokenlari javob limitini yeb qo'yadi — past rejimga qo'yamiz."""
    # TUZATILDI: model None bo'lsa, xato bermaslik uchun
    model = payload.get("model") or ""
    if model.startswith("openai/gpt-oss") and config.REASONING_EFFORT in ("low", "medium", "high"):
        payload["reasoning_effort"] = config.REASONING_EFFORT


# ---------------------------------------------------------------- Groq

def _groq_text(data: dict, name: str = "Groq") -> str:
    # TUZATILDI: data None bo'lsa, xato bermaslik uchun
    if not isinstance(data, dict):
        raise LLMError(f"{name}: javob JSON emas.")
    choices = data.get("choices") or []
    if not choices:
        err = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else None
        raise LLMError(f"{name}: " + (str(err)[:150] if err else "bo'sh javob qaytardi."))
    choice = choices[0]
    text = _clean((choice.get("message") or {}).get("content") or "")
    if not text:
        if choice.get("finish_reason") == "length":
            raise LLMError("Javob token limitiga sig'madi. /clear qiling yoki savolni qisqaroq yozing.")
        raise LLMError("Model javob yarata olmadi, savolni boshqacha yozing.")
    return text


def _chat_groq(messages: list[dict], system: str, image_b64: str | None, max_tokens: int | None = None) -> str:
    msgs = [{"role": "system", "content": system}] + [dict(m) for m in messages]
    model = config.CHAT_MODEL
    if image_b64:
        model = config.VISION_MODEL
        msgs[-1]["content"] = [
            {"type": "text", "text": msgs[-1]["content"]},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}},
        ]
    payload = {
        "model": model,
        "messages": msgs,
        "temperature": 0.7,
        "max_completion_tokens": max_tokens or config.CHAT_MAX_TOKENS,
    }
    _apply_reasoning(payload)
    resp = _call(
        "Groq",
        lambda: _session.post(
            GROQ_BASE + "/chat/completions",
            headers={"Authorization": "Bearer " + config.GROQ_API_KEY},
            json=payload,
            timeout=max(config.REQUEST_TIMEOUT, 90) if max_tokens else config.REQUEST_TIMEOUT,
        ),
    )
    return _groq_text(resp.json())


# ---------------------------------------------------------------- OpenRouter

def _chat_openrouter(messages: list[dict], system: str, image_b64: str | None, max_tokens: int | None = None) -> str:
    model = config.OPENROUTER_MODEL
    msgs = [{"role": "system", "content": system}] + [dict(m) for m in messages]
    if image_b64:
        model = config.OPENROUTER_VISION_MODEL
        msgs[-1]["content"] = [
            {"type": "text", "text": msgs[-1]["content"]},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}},
        ]
    payload = {
        "model": model,
        "messages": msgs,
        "temperature": 0.7,
        "max_tokens": max_tokens or config.CHAT_MAX_TOKENS,
    }
    resp = _call(
        "OpenRouter",
        lambda: _session.post(
            OPENROUTER_URL,
            headers={"Authorization": "Bearer " + config.OPENROUTER_API_KEY, "X-Title": "AI Yordamchi"},
            json=payload,
            timeout=max(config.REQUEST_TIMEOUT, 90),
        ),
        attempts=2,
    )
    return _groq_text(resp.json(), "OpenRouter")


# ---------------------------------------------------------------- Gemini (zaxira)

def _chat_gemini(messages: list[dict], system: str, image_b64: str | None, max_tokens: int | None = None) -> str:
    # TUZATILDI: messages bo'sh bo'lsa, xato bermaslik uchun
    if not messages:
        raise LLMError("Gemini: xabarlar ro'yxati bo'sh.")
    contents = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    if image_b64:
        # TUZATILDI: rasm faqat user xabariga qo'shiladi
        for i in range(len(contents) - 1, -1, -1):
            if contents[i]["role"] == "user":
                contents[i]["parts"].append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
                break
        else:
            # Agar user xabari bo'lmasa, oxirgi xabarga qo'shamiz
            contents[-1]["parts"].append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
    gen_cfg = {"temperature": 0.7, "maxOutputTokens": max_tokens or config.CHAT_MAX_TOKENS}
    if "flash" in config.GEMINI_MODEL:
        gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}  # "o'ylash" tokenlari javobni kesmasin
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": gen_cfg,
    }
    resp = _call(
        "Gemini",
        lambda: _session.post(
            GEMINI_URL.format(model=config.GEMINI_MODEL),
            headers={"x-goog-api-key": config.GEMINI_API_KEY},
            json=payload,
            timeout=config.REQUEST_TIMEOUT,
        ),
    )
    data = resp.json()
    if (data.get("promptFeedback") or {}).get("blockReason"):
        raise LLMError("Bu so'rovga xavfsizlik siyosati tufayli javob berib bo'lmaydi.")
    candidates = data.get("candidates") or []
    if not candidates:
        raise LLMError("Gemini bo'sh javob qaytardi.")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise LLMError("Gemini javob yarata olmadi, savolni boshqacha yozing.")
    return text


# ---------------------------------------------------------------- ommaviy API

def _provider_order(task: str, has_image: bool) -> list[str]:
    """Vazifaga qarab xizmatlar tartibi: asosiysi birinchi, qolganlari zaxira."""
    avail = {
        "groq": True,
        "openrouter": bool(config.OPENROUTER_API_KEY) and not (has_image and not config.OPENROUTER_VISION_MODEL),
        "gemini": bool(config.GEMINI_API_KEY),
    }
    pref = config.FILE_PROVIDER if task == "file" else config.CHAT_PROVIDER
    order = [pref] + [p for p in ("groq", "openrouter", "gemini") if p != pref]
    return [p for p in order if avail[p]]


def chat(history: list[dict], user_text: str, system: str, image: bytes | None = None,
         max_tokens: int | None = None, task: str = "chat") -> str:
    """Javob beradi. task="chat" — suhbat, task="file" — fayl/taqdimot. Asosiy xizmat xato bersa,
    keyingisiga (Groq → OpenRouter → Gemini yoki teskari) avtomatik o'tadi."""
    messages = history + [{"role": "user", "content": user_text}]
    image_b64 = base64.b64encode(image).decode() if image else None
    funcs = {"groq": _chat_groq, "openrouter": _chat_openrouter, "gemini": _chat_gemini}
    first_error = None
    for provider in _provider_order(task, bool(image)):
        try:
            text = funcs[provider](messages, system, image_b64, max_tokens)
            log.info("Javob: %s (%s)", provider, task)
            return text
        except LLMError as exc:
            first_error = first_error or exc
            log.warning("%s ishlamadi (%s) — keyingi xizmatga o'tiladi", provider, exc)
    raise first_error or LLMError("Hech bir AI xizmati javob bermadi.")


def classify(text: str, has_image: bool, has_doc: bool = False) -> tuple[str, str]:
    """('chat' | 'draw' | 'edit' | 'file', inglizcha prompt)."""
    payload = {
        "model": config.CLASSIFIER_MODEL,
        "temperature": 0,
        "max_completion_tokens": 600,
        "messages": [
            {"role": "system", "content": CLASSIFIER_PROMPT},
            {"role": "user",
             "content": f"has_image: {str(has_image).lower()}\nhas_doc: {str(has_doc).lower()}\nxabar: {text}"},
        ],
    }
    _apply_reasoning(payload)
    resp = _call(
        "Groq",
        lambda: _session.post(
            GROQ_BASE + "/chat/completions",
            headers={"Authorization": "Bearer " + config.GROQ_API_KEY},
            json=payload,
            timeout=config.REQUEST_TIMEOUT,
        ),
        attempts=2,
    )
    # TUZATILDI: JSON xato bo'lsa, xato bermaslik uchun
    try:
        content = _groq_text(resp.json())
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("Classify: JSON xato: %s", exc)
        return "chat", ""
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        return "chat", ""
    try:
        data = json.loads(content[start:end + 1])
    except json.JSONDecodeError:
        return "chat", ""
    action = data.get("action", "chat")
    prompt = str(data.get("prompt") or "").strip()[:600]
    if action not in ("chat", "draw", "edit", "file"):
        action = "chat"
    if action in ("draw", "edit") and not prompt:
        action = "chat"
    if action == "edit" and not has_image:
        action = "chat"
    return action, ("" if action == "file" else prompt)


def refine_prompt(text: str) -> str:
    """/rasm uchun: o'zbekcha tavsifni inglizcha promptga aylantiradi (xato bo'lsa — asl matn)."""
    payload = {
        "model": config.CLASSIFIER_MODEL,
        "temperature": 0.3,
        "max_completion_tokens": 500,
        "messages": [
            {"role": "system", "content": REFINER_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    _apply_reasoning(payload)
    try:
        resp = _call(
            "Groq",
            lambda: _session.post(
                GROQ_BASE + "/chat/completions",
                headers={"Authorization": "Bearer " + config.GROQ_API_KEY},
                json=payload,
                timeout=config.REQUEST_TIMEOUT,
            ),
            attempts=2,
        )
        return _groq_text(resp.json())[:600] or text
    except Exception as exc:  # noqa: BLE001 — bu yordamchi qadam, asosiy ishni to'xtatmasin
        log.warning("Prompt aniqlashtirilmadi: %s", exc)
        return text


def transcribe(audio: bytes, filename: str = "voice.ogg") -> str:
    """Ovozli xabarni Whisper orqali matnga aylantiradi."""
    form = {"model": config.STT_MODEL, "response_format": "json", "temperature": "0"}
    if config.STT_LANGUAGE:
        form["language"] = config.STT_LANGUAGE
    resp = _call(
        "Groq",
        lambda: _session.post(
            GROQ_BASE + "/audio/transcriptions",
            headers={"Authorization": "Bearer " + config.GROQ_API_KEY},
            files={"file": (filename, audio, "audio/ogg")},
            data=form,
            timeout=config.REQUEST_TIMEOUT,
        ),
        attempts=2,
    )
    # TUZATILDI: JSON xato bo'lsa, xato bermaslik uchun
    try:
        return str(resp.json().get("text") or "").strip()
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("Transcribe: JSON xato: %s", exc)
        return ""
