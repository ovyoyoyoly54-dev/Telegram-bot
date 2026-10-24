"""LLM mijozi: Groq + OpenRouter + Gemini va Internetdan qidirish."""
import base64
import json
import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from ddgs import DDGS

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
    "draw: foydalanuvchi tavsif asosida YANGI rasm chizishni so'raydi. "
    "edit: foydalanuvchi MAVJUD rasmni o'zgartirishni so'raydi; faqat has_image true bo'lsa edit tanla. "
    "file: foydalanuvchi FAYL yaratishni yoki yuborilgan faylni tahrirlashni so'raydi. "
    "chat: qolgan hamma narsa. "
    "prompt: faqat draw/edit uchun — INGLIZCHA ko'rsatma."
)

REFINER_PROMPT = (
    "Foydalanuvchining rasm tavsifini rasm yaratish modeli uchun qisqa, aniq INGLIZCHA promptga "
    "aylantir. Faqat promptning o'zini yoz, boshqa hech narsa yozma."
)

MERGER_PROMPT = """Sen bir nechta AI modelning javoblarini birlashtiruvchi mutaxassissan.
Senga bir xil savolga bir nechta model javoblari beriladi. Ularni tahlil qilib, YAKUNIY, TO'LIQ va ANIQ javob yoz.
QOIDALAR:
- Eng to'g'ri va to'liq ma'lumotlarni tanla.
- Qarama-qarshilik bo'lsa, 2+ model bir xil aytgan versiyani tanla.
- Foydalanuvchi tilida yoz.
JAVOB:"""

PRESENTATION_PROMPT = """Sen professional taqdimot dizayneri va kontent yozuvchisisan.
Foydalanuvchi bergan mavzu asosida TO'LIQ, CHUQUR va RASMLAR BILAN BOYITILGAN taqdimot JSON tuzilmasini yaratasan.
QAT'IY QOIDALAR:
1. SLAYDLAR SONI: Kamida 8 ta, ko'pi bilan 12 ta slayd yarat.
2. RASMLAR: HAR BIR slaydda "image_prompt" maydoni BO'LISHI SHART (ingliz tilida).
FAQAT JSON qaytar. Boshqa hech narsa yozma. Format:
{"title": "...", "theme": "midnight", "slides": [{"layout": "title", "title": "...", "subtitle": "...", "image_prompt": "..."}]}"""


class LLMError(Exception):
    """Foydalanuvchiga ko'rsatish mumkin bo'lgan xato."""


# ================================================================ UMUMIY SO'ROV

def _retry_after(resp, default):
    try:
        return float(resp.headers.get("retry-after", default))
    except (TypeError, ValueError):
        return default


def _call(name: str, send, attempts: int = 3):
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
            raise LLMError(f"{name}: API kalit noto'g'ri.")
        if code == 402:
            raise LLMError(f"{name}: hisobda mablag' yetarli emas.")
        if code == 404:
            raise LLMError(f"{name}: model topilmadi.")
        if code == 413:
            raise LLMError("So'rov juda katta.")
        if code == 400:
            raise LLMError(f"{name} so'rovni qabul qilmadi (400).")
        if code == 429:
            wait = _retry_after(resp, 3 * (i + 1))
            last = LLMError("So'rovlar limiti tugadi, birozdan keyin urinib ko'ring.")
            if wait <= 8 and i < attempts - 1:
                time.sleep(wait)
                continue
            raise last
        if code >= 500:
            last = LLMError(f"{name} serveri band.")
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
        raise LLMError(f"{name}: bo'sh javob qaytardi.")
    choice = choices[0]
    text = _clean((choice.get("message") or {}).get("content") or "")
    if not text:
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
        raise LLMError("Gemini: xabarlar bo'sh.")
    contents = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    if image_b64:
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
    avail = {
        "groq": bool(config.GROQ_API_KEY),
        "openrouter": bool(config.OPENROUTER_API_KEY),
        "gemini": bool(config.GEMINI_API_KEY),
    }
    if has_image:
        avail["openrouter"] = avail["openrouter"] and bool(config.OPENROUTER_VISION_MODEL)
    return [p for p in ("groq", "openrouter", "gemini") if avail[p]]


def _provider_order(task: str, has_image: bool) -> list[str]:
    pref = config.FILE_PROVIDER if task == "file" else config.CHAT_PROVIDER
    return _available_providers(task, has_image)


def _call_one(provider, messages, system, image_b64, max_tokens, temperature):
    func = _PROVIDER_FUNCS.get(provider)
    if not func:
        raise LLMError(f"Noma'lum provayder: {provider}")
    return func(messages, system, image_b64, max_tokens, temperature)


def _parallel_calls(messages, system, image_b64, max_tokens, temperature, task, has_image):
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


# ================================================================ ASOSIY CHAT

def chat(history, user_text, system, image=None, max_tokens=None, task="chat", temperature=None):
    is_presentation = task == "file" and _PRESENTATION_HINTS.search(user_text or "")
    if is_presentation:
        system = PRESENTATION_PROMPT + ("\n\n" + system if system else "")
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


def _merge_answers(user_text: str, results: dict[str, str]) -> str:
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
    if temperature is None:
        temperature = 0.2 if task == "chat" else 0.5
    messages = history + [{"role": "user", "content": user_text}]
    image_b64 = base64.b64encode(image).decode() if image else None

    results = _parallel_calls(messages, system, image_b64, max_tokens, temperature, task, bool(image))
    if not results:
        raise LLMError("Hech bir AI xizmati javob bermadi.")
    log.info("Javoblar: %s", list(results.keys()))
    return _merge_answers(user_text, results)


# ================================================================ INTERNETDAN QIDIRISH

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
    
    try:
        return chat_multi(history, user_text, search_system, max_tokens=max_tokens, task=task, temperature=temperature)
    except Exception as exc:
        log.warning("chat_with_search ichida xato, oddiy chatga o'tiladi: %s", exc)
        return chat(history, user_text, search_system, max_tokens=max_tokens, task=task, temperature=temperature)


# ================================================================ TASNIFLASH

def _classify_via(provider: str, text: str, has_image: bool, has_doc: bool) -> tuple[str, str]:
    user_content = f"has_image: {str(has_image).lower()}\nhas_doc: {str(has_doc).lower()}\nxabar: {text}"
    try:
        res_text = chat([], text, CLASSIFIER_PROMPT, max_tokens=600, temperature=0)
        start, end = res_text.find("{"), res_text.rfind("}")
        if start >= 0 and end >= start:
            data = json.loads(res_text[start:end + 1])
            action = data.get("action", "chat")
            prompt = str(data.get("prompt") or "").strip()[:600]
            if action not in ("chat", "draw", "edit", "file"): 
                action = "chat"
            if action in ("draw", "edit") and not prompt: 
                action = "chat"
            if action == "edit" and not has_image: 
                action = "chat"
            return action, ("" if action == "file" else prompt)
    except Exception:
        pass
    return "chat", ""


def classify(text: str, has_image: bool, has_doc: bool = False) -> tuple[str, str]:
    return _classify_via("groq", text, has_image, has_doc)


def classify_vote(text: str, has_image: bool, has_doc: bool = False) -> tuple[str, str]:
    return classify(text, has_image, has_doc)


# ================================================================ YORDAMCHI

def refine_prompt(text: str) -> str:
    try:
        return chat([], text, REFINER_PROMPT, max_tokens=500, temperature=0.3)[:600] or text
    except Exception:
        return text


def transcribe(audio: bytes, filename: str = "voice.ogg") -> str:
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
    except Exception:
        return ""