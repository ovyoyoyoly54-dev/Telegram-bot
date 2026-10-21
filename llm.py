"""LLM mijozi: Groq + OpenRouter + Gemini.

Imkoniyatlar:
- Chat (bitta provayder, fallback zanjiri bilan)
- chat_multi (barcha provayderlardan parallel javob olib, birlashtirish)
- classify / classify_vote (matnni tasniflash, ko'pchilik ovoz bilan)
- Rasm tushunish, ovoz → matn, rasm promptini aniqlashtirish
- Taqdimot uchun maxsus PRESENTATION_PROMPT
"""
import base64
import json
import logging
import re
import time
from ddgs import DDGS
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import config

log = logging.getLogger("llm")

GROQ_BASE = "https://api.groq.com/openai/v1"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_session = requests.Session()
_THINK = re.compile(r"<think>.*?</think>", re.S)
_PRESENTATION_HINTS = re.compile(
    r"(taqdimot|prezentatsiya|presentation|slayd|slide|pptx|powerpoint|taqdimat)", re.I
)


# ================================================================ PROMPTLAR
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
    "chat: qolgan hamma narsa. "
    "prompt: faqat draw/edit uchun — rasm modeli uchun qisqa INGLIZCHA ko'rsatma. "
    "chat va file bo'lsa bo'sh qoldir."
)
# ================================================================ WEB SEARCH

def web_search(query: str, max_results: int = 5) -> str:
    """DuckDuckGo orqali internetdan qidiradi."""
    try:
        results = DDGS().text(query, max_results=max_results)
        if not results:
            log.warning("Qidiruv natija bermadi: %s", query)
            return ""
        
        formatted = "INTERNET QIDIRUV NATIJALARI:\n"
        for i, r in enumerate(results, 1):
            formatted += f"{i}. {r.get('title', '')}\n"
            formatted += f"   {r.get('body', '')}\n"
            formatted += f"   Manba: {r.get('href', '')}\n\n"
        log.info("Qidiruv muvaffaqiyatli: %s ta natija topildi", len(results))
        return formatted
    except Exception as exc:
        log.warning("Qidiruv xatosi: %s", exc)
        return ""

def chat_with_search(history, user_text, system, max_tokens=None, task="chat", temperature=None):
    """Real-vaqt savollari uchun: avval qidiradi, keyin javob beradi."""
    log.info("Internetdan qidirilmoqda: %s", user_text)
    search_data = web_search(user_text)
    
    # System promptni majburan o'zgartiramiz
    search_system = system + "\n\n"
    if search_data:
        search_system += search_data + "\n\n"
        search_system += (
            "MUHIM QOIDA: Yuqoridagi internet qidiruv natijalaridan foydalanib, foydalanuvchiga ANIQ va TO'G'RI javob ber. "
            "Sen hozir internetga ulangansan. 'Men internetga ulanmaganman' yoki 'real-vaqtni bilmayman' deb javob berish QAT'IYAN TAQIQLANADI. "
            "Faqat berilgan qidiruv natijalariga tayanib javob yoz."
        )
    else:
        search_system += (
            "MUHIM QOIDA: Internetdan qidiruv natija bermadi. Foydalanuvchiga 'internetdan aniq ma'lumot topa olmadim' deb aytishing mumkin, "
            "lekin 'men internetsizman' dema. O'zing bilgan eng so'nggi ma'lumotni ayt."
        )
    
    # Javobni olish (agar chat_multi ishlamasa, oddiy chatga qaytadi)
    try:
        return chat_multi(history, user_text, search_system, max_tokens=max_tokens, task=task, temperature=temperature)
    except Exception as exc:
        log.warning("chat_with_search ichida xato, oddiy chatga o'tiladi: %s", exc)
        return chat(history, user_text, search_system, max_tokens=max_tokens, task=task, temperature=temperature)

REFINER_PROMPT = (
    "Foydalanuvchining rasm tavsifini rasm yaratish modeli uchun qisqa, aniq INGLIZCHA promptga "
    "aylantir. Faqat promptning o'zini yoz, boshqa hech narsa yozma."
)

MERGER_PROMPT = """Sen bir nechta AI modelning javoblarini birlashtiruvchi mutaxassissan.
Senga bir xil savolga bir nechta model javoblari beriladi.
Ularni tahlil qilib, YAKUNIY, TO'LIQ va ANIQ javob yoz.

QOIDALAR:
- Eng to'g'ri va to'liq ma'lumotlarni tanla.
- Qarama-qarshilik bo'lsa, 2+ model bir xil aytgan versiyani tanla.
- Bo'sh yoki umumiy gaplarni olib tashla.
- Javobni bitta, tabiiy matnda yoz (model nomlarini ko'rsatma).
- Foydalanuvchi tilida yoz (o'zbek, rus yoki ingliz).
- Javob uzunligi — eng uzun model javobidan oshmasin.

JAVOB:"""

PRESENTATION_PROMPT = """Sen professional taqdimot dizayneri va kontent yozuvchisisan.
Foydalanuvchi bergan mavzu asosida TO'LIQ, CHUQUR va RASMLAR BILAN BOYITILGAN taqdimot JSON tuzilmasini yaratasan.

QAT'IY QOIDALAR (buzilmasligi shart):

1. SLAYDLAR SONI:
- Foydalanuvchi aniq son aytsa (masalan "8 slaydli"), AYNAN shu sonni yarat.
- Aks holda KAMIDA 8 ta, KO'PI bilan 12 ta slayd yarat.
- 5 tadan kam slayd yaratish TAQIQLANADI.
- Har bir kichik mavzuga alohida slayd ajrat.

2. SLAYD TARKIBI (majburiy ketma-ketlik):
- 1-slayd: layout="title" — mavzu nomi + subtitle (qisqa ta'rif)
- 2-slayd: layout="section" yoki "bullets" — kirish
- 3..(N-2)-slaydlar: mavzuning har bir jihati (bullets, image_text, two_column, stats, steps, chart, table)
- Oxirgi slayd: layout="closing" — xulosa yoki chaqiriq

3. RASMLAR (ENG MUHIM QOIDA):
- HAR BIR slaydda "image_prompt" maydoni BO'LISHI SHART (title, section, closing ham).
- "image_prompt" INGLIZ TILIDA yoziladi, 15-30 so'z.
- Rasm slayd mazmuniga ANIQ mos bo'lsin: asosiy tushuncha, ob'ekt yoki metafora tasvirlansin.
- Har bir rasm UNIKAL bo'lsin. Umumiy so'zlardan qochma.
- Misol: "A futuristic hospital room with a holographic AI assistant diagnosing a patient, blue and white tones, cinematic lighting, photorealistic"

4. MATN SIFATI:
- Har bir bullet 8-15 so'z, "Sarlavha: izoh" formatida.
- Bo'sh gaplardan qoch ("Bu juda muhim" kabi).

5. LAYOUTLAR:
title, section, bullets, image_text, two_column, stats, steps, chart, table, quote, closing

FAQAT JSON qaytar. Boshqa hech narsa yozma. Format:
{"title": "...", "theme": "midnight", "slides": [
  {"layout": "title", "title": "...", "subtitle": "...", "image_prompt": "..."},
  {"layout": "bullets", "title": "...", "bullets": ["...", "..."], "image_prompt": "..."}
]}

Themelar: midnight (IT), ocean (ta'lim), sunset (ijodiy), forest (ekologiya), royal (biznes), minimal (rasmiy).
Faqat JSON. ``` belgilarini qo'shma.
"""


class LLMError(Exception):
    """Foydalanuvchiga ko'rsatish mumkin bo'lgan xato."""


# ================================================================ UMUMIY SO'ROV

def _retry_after(resp, default):
    try:
        return float(resp.headers.get("retry-after", default))
    except (TypeError, ValueError):
        return default


def _call(name: str, send, attempts: int = 3):
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
            raise LLMError(f"{name}: hisobda mablag' yetarli emas.")
        if code == 404:
            raise LLMError(f"{name}: model topilmadi. Model nomini (env) tekshiring.")
        if code == 413:
            raise LLMError("So'rov juda katta. /clear qiling yoki kichikroq rasm yuboring.")
        if code == 400:
            if "API key" in resp.text:
                raise LLMError(f"{name}: API kalit noto'g'ri.")
            raise LLMError(f"{name} so'rovni qabul qilmadi (400).")
        if code == 429:
            wait = _retry_after(resp, 3 * (i + 1))
            last = LLMError("So'rovlar limiti tugadi, birozdan keyin urinib ko'ring.")
            if wait <= 8 and i < attempts - 1:
                time.sleep(wait)
                continue
            raise last
        if code >= 500:
            last = LLMError(f"{name} serveri band, birozdan keyin urinib ko'ring.")
            time.sleep(2 * (i + 1))
            continue
        raise LLMError(f"{name}: kutilmagan xato (kod {code}).")
    raise last


def _clean(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def _apply_reasoning(payload: dict) -> None:
    model = payload.get("model") or ""
    if model.startswith("openai/gpt-oss") and config.REASONING_EFFORT in ("low", "medium", "high"):
        payload["reasoning_effort"] = config.REASONING_EFFORT


# ================================================================ GROQ

def _groq_text(data: dict, name: str = "Groq") -> str:
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
            raise LLMError("Javob token limitiga sig'madi. /clear qiling yoki qisqaroq yozing.")
        raise LLMError("Model javob yarata olmadi.")
    return text


def _chat_groq(messages, system, image_b64, max_tokens=None, temperature=0.2):
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
        "temperature": temperature,
        "max_completion_tokens": max_tokens or config.CHAT_MAX_TOKENS,
    }
    _apply_reasoning(payload)
    resp = _call(
        "Groq",
        lambda: _session.post(
            GROQ_BASE + "/chat/completions",
            headers={"Authorization": "Bearer " + config.GROQ_API_KEY},
            json=payload,
            timeout=max(config.REQUEST_TIMEOUT, 120) if max_tokens and max_tokens > 4000 else config.REQUEST_TIMEOUT,
        ),
    )
    return _groq_text(resp.json())


# ================================================================ OPENROUTER

def _chat_openrouter(messages, system, image_b64, max_tokens=None, temperature=0.2):
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
        "temperature": temperature,
        "max_tokens": max_tokens or config.CHAT_MAX_TOKENS,
    }
    resp = _call(
        "OpenRouter",
        lambda: _session.post(
            OPENROUTER_URL,
            headers={"Authorization": "Bearer " + config.OPENROUTER_API_KEY, "X-Title": "AI Yordamchi"},
            json=payload,
            timeout=max(config.REQUEST_TIMEOUT, 120) if max_tokens and max_tokens > 4000 else config.REQUEST_TIMEOUT,
        ),
        attempts=2,
    )
    return _groq_text(resp.json(), "OpenRouter")


# ================================================================ GEMINI

def _chat_gemini(messages, system, image_b64, max_tokens=None, temperature=0.2):
    if not messages:
        raise LLMError("Gemini: xabarlar ro'yxati bo'sh.")
    contents = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    if image_b64:
        for i in range(len(contents) - 1, -1, -1):
            if contents[i]["role"] == "user":
                contents[i]["parts"].append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
                break
        else:
            contents[-1]["parts"].append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
    gen_cfg = {"temperature": temperature, "maxOutputTokens": max_tokens or config.CHAT_MAX_TOKENS}
    if "flash" in config.GEMINI_MODEL:
        gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
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
        raise LLMError("Gemini javob yarata olmadi.")
    return text


_PROVIDER_FUNCS = {
    "groq": _chat_groq,
    "openrouter": _chat_openrouter,
    "gemini": _chat_gemini,
}


# ================================================================ PROVAYDERLAR

def _available_providers(task: str, has_image: bool) -> list[str]:
    """Barcha mavjud provayderlar (fallback emas — parallel ishlatish uchun)."""
    avail = {
        "groq": bool(config.GROQ_API_KEY),
        "openrouter": bool(config.OPENROUTER_API_KEY),
        "gemini": bool(config.GEMINI_API_KEY),
    }
    if has_image:
        avail["openrouter"] = avail["openrouter"] and bool(config.OPENROUTER_VISION_MODEL)
    return [p for p in ("groq", "openrouter", "gemini") if avail[p]]


def _provider_order(task: str, has_image: bool) -> list[str]:
    """Fallback zanjiri: asosiysi birinchi, qolganlari zaxira."""
    avail = {
        "groq": True,
        "openrouter": bool(config.OPENROUTER_API_KEY) and not (has_image and not config.OPENROUTER_VISION_MODEL),
        "gemini": bool(config.GEMINI_API_KEY),
    }
    pref = config.FILE_PROVIDER if task == "file" else config.CHAT_PROVIDER
    order = [pref] + [p for p in ("groq", "openrouter", "gemini") if p != pref]
    return [p for p in order if avail[p]]


def _call_one(provider, messages, system, image_b64, max_tokens, temperature):
    """Bitta provayderga so'rov (parallel ishlatish uchun)."""
    func = _PROVIDER_FUNCS.get(provider)
    if not func:
        raise LLMError(f"Noma'lum provayder: {provider}")
    return func(messages, system, image_b64, max_tokens, temperature)


def _parallel_calls(messages, system, image_b64, max_tokens, temperature, task, has_image):
    """Barcha mavjud provayderlarga parallel so'rov yuboradi. {provider: text} qaytaradi."""
    providers = _available_providers(task, has_image)
    if not providers:
        raise LLMError("Hech qanday API kaliti sozlanmagan.")
    if len(providers) == 1:
        p = providers[0]
        try:
            return {p: _call_one(p, messages, system, image_b64, max_tokens, temperature)}
        except LLMError as exc:
            log.warning("%s xato: %s", p, exc)
            return {}

    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(providers)) as ex:
        futures = {
            ex.submit(_call_one, p, messages, system, image_b64, max_tokens, temperature): p
            for p in providers
        }
        for fut in as_completed(futures, timeout=config.REQUEST_TIMEOUT + 60):
            p = futures[fut]
            try:
                results[p] = fut.result()
                log.info("✓ %s javob berdi", p)
            except Exception as exc:
                log.warning("✗ %s xato: %s", p, exc)
    return results


# ================================================================ CHAT (fallback)

def chat(history, user_text, system, image=None, max_tokens=None, task="chat", temperature=None):
    """Javob beradi. Fallback zanjiri: asosiy provayder → zaxira."""
    is_presentation = task == "file" and _PRESENTATION_HINTS.search(user_text or "")
    if is_presentation:
        system = PRESENTATION_PROMPT + ("\n\nQO'SHIMCHA:\n" + system if system else "")
        if max_tokens is None or max_tokens < config.PRESENTATION_MAX_TOKENS:
            max_tokens = config.PRESENTATION_MAX_TOKENS
        if temperature is None:
            temperature = 0.7
    else:
        if temperature is None:
            temperature = 0.2 if task == "chat" else 0.5

    messages = history + [{"role": "user", "content": user_text}]
    image_b64 = base64.b64encode(image).decode() if image else None
    first_error = None
    for provider in _provider_order(task, bool(image)):
        try:
            text = _call_one(provider, messages, system, image_b64, max_tokens, temperature)
            log.info("Javob: %s (%s, t=%.1f)", provider, task, temperature)
            return text
        except LLMError as exc:
            first_error = first_error or exc
            log.warning("%s ishlamadi: %s", provider, exc)
    raise first_error or LLMError("Hech bir AI xizmati javob bermadi.")


# ================================================================ CHAT_MULTI (parallel + merge)

def _merge_answers(user_text: str, results: dict[str, str]) -> str:
    """Bir nechta model javobini bitta javobga birlashtiradi (Groq orqali)."""
    if not results:
        raise LLMError("Birlashtirish uchun javob yo'q.")
    if len(results) == 1:
        return next(iter(results.values()))

    parts = [f"### Model: {p}\n{text}" for p, text in results.items()]
    combined = f"Savol: {user_text}\n\n" + "\n\n".join(parts)
    payload = {
        "model": config.CLASSIFIER_MODEL,
        "temperature": 0.3,
        "max_completion_tokens": config.CHAT_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": MERGER_PROMPT},
            {"role": "user", "content": combined},
        ],
    }
    _apply_reasoning(payload)
    try:
        resp = _call(
            "Groq (merger)",
            lambda: _session.post(
                GROQ_BASE + "/chat/completions",
                headers={"Authorization": "Bearer " + config.GROQ_API_KEY},
                json=payload,
                timeout=max(config.REQUEST_TIMEOUT, 90),
            ),
            attempts=2,
        )
        return _groq_text(resp.json(), "Merger")
    except LLMError as exc:
        log.warning("Merge xato, eng uzun javob qaytariladi: %s", exc)
        return max(results.values(), key=len)


def chat_multi(history, user_text, system, image=None, max_tokens=None, task="chat", temperature=None):
    """Barcha provayderlardan parallel javob olib, birlashtiradi (suhbat uchun)."""
    if temperature is None:
        temperature = 0.2 if task == "chat" else 0.5
    messages = history + [{"role": "user", "content": user_text}]
    image_b64 = base64.b64encode(image).decode() if image else None

    results = _parallel_calls(messages, system, image_b64, max_tokens, temperature, task, bool(image))
    if not results:
        raise LLMError("Hech bir AI xizmati javob bermadi.")
    log.info("Javoblar: %s", list(results.keys()))
    return _merge_answers(user_text, results)


# ================================================================ TASNIFLASH

def _classify_via(provider: str, text: str, has_image: bool, has_doc: bool) -> tuple[str, str]:
    """Bitta provayder orqali tasniflash. (action, prompt) qaytaradi."""
    user_content = f"has_image: {str(has_image).lower()}\nhas_doc: {str(has_doc).lower()}\nxabar: {text}"

    if provider == "groq":
        payload = {
            "model": config.CLASSIFIER_MODEL,
            "temperature": 0,
            "max_completion_tokens": 600,
            "messages": [
                {"role": "system", "content": CLASSIFIER_PROMPT},
                {"role": "user", "content": user_content},
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
        content = _groq_text(resp.json())

    elif provider == "openrouter":
        payload = {
            "model": config.OPENROUTER_MODEL,
            "temperature": 0,
            "max_tokens": 600,
            "messages": [
                {"role": "system", "content": CLASSIFIER_PROMPT},
                {"role": "user", "content": user_content},
            ],
        }
        resp = _call(
            "OpenRouter",
            lambda: _session.post(
                OPENROUTER_URL,
                headers={"Authorization": "Bearer " + config.OPENROUTER_API_KEY, "X-Title": "AI Yordamchi"},
                json=payload,
                timeout=config.REQUEST_TIMEOUT,
            ),
            attempts=2,
        )
        content = _groq_text(resp.json(), "OpenRouter")

    elif provider == "gemini":
        payload = {
            "system_instruction": {"parts": [{"text": CLASSIFIER_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_content}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 600,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        resp = _call(
            "Gemini",
            lambda: _session.post(
                GEMINI_URL.format(model=config.GEMINI_MODEL),
                headers={"x-goog-api-key": config.GEMINI_API_KEY},
                json=payload,
                timeout=config.REQUEST_TIMEOUT,
            ),
            attempts=2,
        )
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError("Gemini bo'sh javob qaytardi.")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        content = "".join(p.get("text", "") for p in parts).strip()
    else:
        raise LLMError(f"Noma'lum provayder: {provider}")

    # JSON ajratish
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


def classify(text: str, has_image: bool, has_doc: bool = False) -> tuple[str, str]:
    """Oddiy tasniflash (faqat asosiy provayder — tez va arzon)."""
    try:
        return _classify_via("groq", text, has_image, has_doc)
    except LLMError as exc:
        log.warning("Groq classify xato, Gemini'ga o'tiladi: %s", exc)
        if config.GEMINI_API_KEY:
            try:
                return _classify_via("gemini", text, has_image, has_doc)
            except LLMError as exc2:
                log.warning("Gemini classify ham xato: %s", exc2)
        return "chat", ""


def classify_vote(text: str, has_image: bool, has_doc: bool = False) -> tuple[str, str]:
    """Bir necha modeldan tasnif olib, ko'pchilik ovoz bilan qaror qabul qiladi."""
    providers = _available_providers("chat", False)  # tasniflash uchun rasm kerak emas
    if len(providers) < 2:
        return classify(text, has_image, has_doc)

    votes: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=len(providers)) as ex:
        futures = {
            ex.submit(_classify_via, p, text, has_image, has_doc): p for p in providers
        }
        for fut in as_completed(futures, timeout=config.REQUEST_TIMEOUT + 15):
            p = futures[fut]
            try:
                votes.append(fut.result())
                log.info("Tasniflash (%s): %s", p, votes[-1])
            except Exception as exc:
                log.warning("Tasniflash (%s) xato: %s", p, exc)

    if not votes:
        return "chat", ""
    if len(votes) == 1:
        return votes[0]

    # Ko'pchilik ovoz
    actions = [a for a, _ in votes]
    action, count = Counter(actions).most_common(1)[0]
    log.info("Ovozlar: %s → %s (%s/%s)", actions, action, count, len(actions))

    # G'olib action uchun eng uzun prompt
    prompts = [p for a, p in votes if a == action and p]
    prompt = max(prompts, key=len) if prompts else ""
    return action, prompt


# ================================================================ RASM PROMPTINI ANIQLASHTIRISH

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
    except Exception as exc:
        log.warning("Prompt aniqlashtirilmadi: %s", exc)
        return text


# ================================================================ OVOZ → MATN

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
    try:
        return str(resp.json().get("text") or "").strip()
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("Transcribe JSON xato: %s", exc)
        return ""