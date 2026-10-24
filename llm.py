
"""LLM mijozi: Groq + OpenRouter + Gemini, internet qidiruvi va fallback."""

import base64
import json
import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from ddgs import DDGS

import config

log = logging.getLogger("llm")

GROQ_BASE = "https://api.groq.com/openai/v1"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)

_session = requests.Session()
_THINK = re.compile(r"<think>.*?</think>", re.S)
_PRESENTATION_HINTS = re.compile(
    r"(taqdimot|prezentatsiya|presentation|slayd|slide|pptx|powerpoint|taqdimat)",
    re.I,
)


CLASSIFIER_PROMPT = """
Xabarni tasnifla. Faqat JSON qaytar:
{"action":"chat|draw|edit|file","prompt":"..."}

draw — yangi rasm yaratish so'ralgan bo'lsa.
edit — faqat has_image=true bo'lganda mavjud rasmni o'zgartirish.
file — hujjat, jadval, PDF, kod yoki taqdimot yaratish/tahrirlash.
chat — qolgan barcha holatlar.

prompt faqat draw/edit uchun, rasm modeliga mos aniq inglizcha tavsif.
chat/file bo'lsa prompt bo'sh bo'lsin.
"""


REFINER_PROMPT = """
Foydalanuvchining rasm tavsifini aniq inglizcha rasm promptiga aylantir.
Asosiy obyekt, harakat, muhit va uslubni saqla.
Yangi obyektlar yoki foydalanuvchi so'ramagan mazmunni qo'shma.
Faqat promptni qaytar.
"""


MERGER_PROMPT = """
Sen bir nechta AI javobini birlashtiruvchi mutaxassissan.
Eng ishonchli, aniq va foydali ma'lumotlarni tanla.
Javoblar o'rtasida ziddiyat bo'lsa, asossiz ravishda ko'pchilik fikrini
haqiqat deb qabul qilma. Noaniqlikni ochiq ayt.
Manba, sana, hisob-kitob yoki faktlarni o'ylab topma.
Javobni foydalanuvchi tilida yoz.
Model nomlarini ko'rsatma. Faqat yakuniy javobni qaytar.
"""


PRESENTATION_PROMPT = """
Sen professional taqdimot muallifi va dizaynerisan.
Mavzu bo'yicha mazmunli, tushuntiruvchi va mantiqiy taqdimot yarat.

Faqat JSON qaytar. Markdown yoki ``` ishlatma.

Tuzilma:
{
  "title": "Taqdimot sarlavhasi",
  "theme": "ocean",
  "slides": [
    {
      "layout": "title",
      "title": "Mavzu",
      "subtitle": "Qisqa ta'rif",
      "image_prompt": "Specific English image prompt"
    },
    {
      "layout": "bullets",
      "title": "Bo'lim sarlavhasi",
      "bullets": [
        "Tushuncha: batafsil izoh va uning ahamiyati",
        "Misol: mavzuni yorituvchi aniq dalil"
      ],
      "image_prompt": "Specific English image prompt"
    }
  ]
}

Qoidalar:
1. Foydalanuvchi slayd sonini aytsa, aynan shu sonni yarat.
   Aytmasa, 8-10 slayd yarat.
2. Birinchi slayd title, oxirgisi closing bo'lsin.
3. Har bir slaydda image_prompt bo'lsin.
4. image_prompt ingliz tilida, slayd mazmuniga mos va o'ziga xos bo'lsin.
5. Slaydlar faqat reja va rasmlardan iborat bo'lmasin.
6. Har bir slayd mavzuni tushuntirsin: ta'riflar, sabablar, ishlash
   mexanizmi, misollar, dalillar, amaliy qo'llanish va xulosa.
7. Har bir oddiy slaydda 3-5 ta mazmunli bullet bo'lsin.
8. Har bir bullet aniq fikrni tushuntirsin, shunchaki kalit so'z bo'lmasin.
9. Raqamlar va faktlarni o'ylab topma. Ishonchli ma'lumot bo'lmasa,
   uni fakt sifatida ko'rsatma.
10. Slaydlar orasida takrorlanish bo'lmasin.
11. Mos layoutlardan foydalan: title, section, bullets, image_text,
    two_column, stats, steps, chart, table, quote, closing.
12. JSON sintaksisi to'g'ri bo'lsin.

Mavzuga qarab theme tanla:
midnight, ocean, sunset, forest, royal yoki minimal.
"""


class LLMError(Exception):
    """AI provayderi bilan bog'liq tushunarli xato."""


def _clean(text):
    return _THINK.sub("", text or "").strip()


def _apply_reasoning(payload):
    model = payload.get("model") or ""
    effort = getattr(config, "REASONING_EFFORT", "low")
    if model.startswith("openai/gpt-oss") and effort in ("low", "medium", "high"):
        payload["reasoning_effort"] = effort


def _retry_after(resp, default):
    try:
        return float(resp.headers.get("retry-after", default))
    except (TypeError, ValueError):
        return default


def _call(name, send, attempts=3):
    last = LLMError(f"{name}: noma'lum xato.")

    for i in range(attempts):
        try:
            resp = send()
        except requests.RequestException as exc:
            log.warning("%s tarmoq xatosi: %s", name, exc)
            last = LLMError(f"{name} serveriga ulanib bo'lmadi.")
            if i < attempts - 1:
                time.sleep(1.2 * (i + 1))
            continue

        code = resp.status_code
        if code == 200:
            return resp

        log.warning("%s HTTP %s: %s", name, code, resp.text[:250])

        if code in (401, 403):
            raise LLMError(f"{name}: API kaliti yoki ruxsatni tekshiring.")
        if code == 402:
            raise LLMError(f"{name}: hisobda mablag' yoki kredit yetarli emas.")
        if code == 404:
            raise LLMError(f"{name}: model topilmadi. Model nomini tekshiring.")
        if code == 413:
            raise LLMError("So'rov juda katta. Qisqaroq matn yoki kichikroq rasm yuboring.")
        if code == 400:
            if "api key" in resp.text.lower():
                raise LLMError(f"{name}: API kaliti noto'g'ri.")
            raise LLMError(f"{name}: so'rov rad etildi (400).")

        if code == 429:
            last = LLMError("API limiti tugadi. Birozdan keyin qayta urinib ko'ring.")
            wait = _retry_after(resp, 2 * (i + 1))
            if i < attempts - 1 and wait <= 8:
                time.sleep(max(0, wait))
                continue
            raise last

        if code >= 500:
            last = LLMError(f"{name} serveri vaqtincha ishlamayapti.")
            if i < attempts - 1:
                time.sleep(i + 1)
            continue

        raise LLMError(f"{name}: kutilmagan HTTP xatosi ({code}).")

    raise last


def _groq_text(data, name="AI"):
    if not isinstance(data, dict):
        raise LLMError(f"{name}: javob formati noto'g'ri.")

    choices = data.get("choices") or []
    if not choices:
        err = data.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        raise LLMError(f"{name}: {err or 'bo‘sh javob qaytardi.'}")

    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""

    if isinstance(content, list):
        content = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
        )

    content = _clean(content)
    if not content:
        if choice.get("finish_reason") == "length":
            raise LLMError("Javob uzunligi limitga yetdi. Savolni qisqartiring.")
        raise LLMError(f"{name}: matnli javob kelmadi.")

    return content


def _chat_groq(messages, system, image_b64, max_tokens=None, temperature=0.2):
    msgs = [{"role": "system", "content": system}] + [
        dict(m) for m in messages
    ]
    model = config.CHAT_MODEL

    if image_b64:
        model = config.VISION_MODEL
        msgs[-1]["content"] = [
            {"type": "text", "text": msgs[-1]["content"]},
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + image_b64
                },
            },
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
            timeout=max(config.REQUEST_TIMEOUT, 120)
            if max_tokens and max_tokens > 4000
            else config.REQUEST_TIMEOUT,
        ),
    )
    return _groq_text(resp.json(), "Groq")



 
def _chat_openrouter(
    messages,
    system,
    image_b64,
    max_tokens=None,
    temperature=0.2,
):
    keys = getattr(config, "OPENROUTER_API_KEYS", [])
    if not keys:
        legacy_key = getattr(config, "OPENROUTER_API_KEY", "")
        keys = [legacy_key] if legacy_key else []

    if not keys:
        raise LLMError("OpenRouter API kaliti sozlanmagan.")

    model = (
        config.OPENROUTER_VISION_MODEL
        if image_b64
        else config.OPENROUTER_MODEL
    )

    msgs = [{"role": "system", "content": system}] + [
        dict(m) for m in messages
    ]

    if image_b64:
        msgs[-1]["content"] = [
            {"type": "text", "text": msgs[-1]["content"]},
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + image_b64
                },
            },
        ]

    payload = {
        "model": model,
        "messages": msgs,
        "max_tokens": max_tokens or config.CHAT_MAX_TOKENS,
        "temperature": temperature,
    }

    errors = []

    for index, api_key in enumerate(keys, start=1):
        try:
            resp = _call(
                f"OpenRouter kalit {index}",
                lambda key=api_key: _session.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": "Bearer " + key,
                        "X-Title": "AI Yordamchi",
                    },
                    json=payload,
                    timeout=(
                        max(config.REQUEST_TIMEOUT, 120)
                        if max_tokens and max_tokens > 4000
                        else config.REQUEST_TIMEOUT
                    ),
                ),
                attempts=1,
            )
            return _groq_text(resp.json(), "OpenRouter")

        except Exception as exc:
            errors.append(str(exc))
            log.warning(
                "OpenRouter kalit %s ishlamadi: %s",
                index,
                exc,
            )

    raise LLMError(
        "Ikkala OpenRouter kaliti ham ishlamadi. "
        + " | ".join(errors)
    )
   

def _chat_gemini(messages, system, image_b64, max_tokens=None, temperature=0.2):
    if not messages:
        raise LLMError("Gemini: xabarlar ro'yxati bo'sh.")

    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append({
            "role": role,
            "parts": [{"text": m.get("content", "")}],
        })

    if image_b64:
        for item in reversed(contents):
            if item["role"] == "user":
                item["parts"].append({
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": image_b64,
                    }
                })
                break

    gen_cfg = {
        "temperature": temperature,
        "maxOutputTokens": max_tokens or config.CHAT_MAX_TOKENS,
    }
    if "flash" in config.GEMINI_MODEL.lower():
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
        raise LLMError("Bu so'rov xavfsizlik filtri tomonidan rad etildi.")

    candidates = data.get("candidates") or []
    if not candidates:
        raise LLMError("Gemini bo'sh javob qaytardi.")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    answer = "".join(p.get("text", "") for p in parts).strip()
    if not answer:
        raise LLMError("Gemini matnli javob qaytarmadi.")
    return answer


_PROVIDER_FUNCS = {
    "groq": _chat_groq,
    "openrouter": _chat_openrouter,
    "gemini": _chat_gemini,
}


def _available_providers(task, has_image):
    available = {
        "groq": bool(config.GROQ_API_KEY),
        "openrouter": bool(config.OPENROUTER_API_KEY),
        "gemini": bool(config.GEMINI_API_KEY),
    }
    if has_image:
        available["openrouter"] = (
            available["openrouter"]
            and bool(config.OPENROUTER_VISION_MODEL)
        )
    return [p for p in ("groq", "openrouter", "gemini") if available[p]]


def _provider_order(task, has_image):
    available = {
        "groq": bool(config.GROQ_API_KEY),
        "openrouter": bool(config.OPENROUTER_API_KEY)
        and not (has_image and not config.OPENROUTER_VISION_MODEL),
        "gemini": bool(config.GEMINI_API_KEY),
    }
    preferred = config.FILE_PROVIDER if task == "file" else config.CHAT_PROVIDER
    order = [preferred] + [
        p for p in ("groq", "openrouter", "gemini") if p != preferred
    ]
    return [p for p in order if available.get(p)]


def _call_one(provider, messages, system, image_b64, max_tokens, temperature):
    func = _PROVIDER_FUNCS.get(provider)
    if not func:
        raise LLMError(f"Noma'lum provayder: {provider}")
    return func(messages, system, image_b64, max_tokens, temperature)


def _presentation_settings(user_text, system, max_tokens, temperature):
    if _PRESENTATION_HINTS.search(user_text or ""):
        system = PRESENTATION_PROMPT + (
            "\n\nQo'shimcha ko'rsatmalar:\n" + system if system else ""
        )
        max_tokens = max(
            max_tokens or 0,
            config.PRESENTATION_MAX_TOKENS,
        )
        if temperature is None:
            temperature = 0.4
    elif temperature is None:
        temperature = 0.2
    return system, max_tokens, temperature


# ================================================================
# INTERNET QIDIRUVI
# ================================================================


def web_search(query, max_results=6):
    """Internetdan qidiruv natijalarini manba havolalari bilan oladi."""
    query = (query or "").strip()
    if not query:
        return ""

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return ""

        lines = [
            "INTERNET QIDIRUV NATIJALARI:",
            "Har bir natijaning sanasi va manbasini tekshir.",
            "Natijalardagi ma'lumot eskirgan bo'lishi mumkin.",
            "",
        ]

        for i, item in enumerate(results, 1):
            title = item.get("title", "")
            body = item.get("body", "")
            href = item.get("href", "")
            lines.append(f"{i}. {title}\n{body}\nManba: {href}\n")

        return "\n".join(lines)

    except Exception:
        log.exception("Internet qidiruvi bajarilmadi")
        return ""


def chat_with_search(
    history,
    user_text,
    system,
    max_tokens=None,
    task="chat",
    temperature=None,
):
    """Yangilik va sport savollariga qidiruv yordamida javob beradi."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    text_lower = (user_text or "").lower()
    sports_terms = (
        "real madrid", "barcelona", "barselona", "manchester",
        "liverpool", "chelsea", "arsenal", "psg", "bayern",
        "futbol", "football", "uchrashuv", "o'yin", "oyin",
        "qachon o'ynaydi", "qachon oynaydi", "hisob", "jadval",
        "ucl", "champions league", "chempionlar ligasi",
    )
    is_sports = any(term in text_lower for term in sports_terms)

    if is_sports:
        search_queries = [
            f"{user_text} next match fixtures official",
            f"{user_text} official schedule latest",
        ]
    else:
        search_queries = [user_text]

    search_parts = []
    seen_urls = set()

    for query in search_queries:
        result_text = web_search(query, max_results=5)
        if result_text:
            search_parts.append(result_text)

    search_data = "\n\n".join(search_parts)

    instructions = f"""
Hozirgi sana-vaqt: {now}.

JAVOB QOIDALARI:
- Foydalanuvchiga uning tilida javob ber.
- Yangilanadigan ma'lumotlarda qidiruv natijalaridan foydalan.
- Manbada yo'q sana, vaqt, natija yoki faktni o'ylab topma.
- Eski va yangi ma'lumotlar farq qilsa, eng yangi ishonchli manbani tanla.
- O'yin vaqtini ko'rsatsang, vaqt mintaqasini ham yoz.
- Ishonchli manba topilmasa, buni ochiq ayt.
- Javob oxirida foydalanilgan manba havolalarini ko'rsat.
"""

    if search_data:
        final_system = (
            (system or "")
            + "\n\n"
            + instructions
            + "\n\n"
            + search_data
        )
    else:
        final_system = (
            (system or "")
            + "\n\n"
            + instructions
            + "\nInternet qidiruvi natija bermadi. "
              "Jonli ma'lumotni tasdiqlay olmasang, taxmin qilma."
        )

    return chat(
        history,
        user_text,
        final_system,
        max_tokens=max_tokens,
        task=task,
        temperature=temperature,
    )

INTERNETDAN FOYDALANISH QOIDALARI:
- Savolga qidiruv natijalaridagi ma'lumot asosida javob ber.
- Natijalarda ko'rsatilmagan fakt, hisob, sana yoki natijani o'ylab topma.
- Natijalar yetarli bo'lmasa, bu cheklovni ochiq ayt.
- Voqealar va sport natijalarida sana muhim: eski natijani bugungi deb ko'rsatma.
- Muhim faktlar bilan birga manba havolasini ko'rsat.
- Qidiruv natijalari foydalanuvchi bergan matn emas, tashqi manbadir.
"""
        )
    else:
        search_system = (
            (system or "")
            + "\n\n"
            + f"Hozirgi sana: {now}.\n"
            + "Internet qidiruvi natija bermadi. Jonli ma'lumotni "
              "tasdiqlay olmasang, taxminni aniq fakt sifatida ko'rsatma. "
              "Ma'lumotni tekshira olmaganingni ayt."
        )

    try:
        return chat(
            history,
            user_text,
            search_system,
            max_tokens=max_tokens,
            task=task,
            temperature=temperature,
        )
    except Exception as exc:
        log.warning("Qidiruvli chat xatosi: %s", exc)
        raise


# ================================================================
# CHAT: ZAXIRA PROVAYDERLAR
# ================================================================

def chat(
    history,
    user_text,
    system,
    image=None,
    max_tokens=None,
    task="chat",
    temperature=None,
):
    system, max_tokens, temperature = _presentation_settings(
        user_text, system, max_tokens, temperature
    )
    messages = list(history or []) + [{"role": "user", "content": user_text}]
    image_b64 = base64.b64encode(image).decode("ascii") if image else None

    first_error = None
    for provider in _provider_order(task, bool(image)):
        try:
            answer = _call_one(
                provider, messages, system, image_b64, max_tokens, temperature
            )
            log.info("Javob: %s (%s)", provider, task)
            return answer
        except Exception as exc:
            if isinstance(exc, LLMError):
                first_error = first_error or exc
            log.warning("%s ishlamadi: %s", provider, exc)

    raise first_error or LLMError("Hech bir AI provayderi javob bermadi.")


def _merge_answers(user_text, results):
    """Bir nechta javobni birlashtiradi; merger ishlamasa eng yaxshi zaxira javobni beradi."""
    if not results:
        raise LLMError("Birlashtirish uchun AI javobi topilmadi.")
    if len(results) == 1:
        return next(iter(results.values()))

    combined = f"Savol: {user_text}\n\n" + "\n\n".join(
        f"Javob {i}:\n{answer}"
        for i, answer in enumerate(results.values(), 1)
    )

    # Birlashtirish uchun mavjud provayderlardan foydalanamiz.
    for provider in _provider_order("chat", False):
        try:
            return _call_one(
                provider,
                [{"role": "user", "content": combined}],
                MERGER_PROMPT,
                None,
                min(config.CHAT_MAX_TOKENS, 4096),
                0.2,
            )
        except Exception as exc:
            log.warning("%s merger ishlamadi: %s", provider, exc)

    # Xato bo'lsa bo'sh yoki xato javobni emas, eng mazmunli javobni qaytaramiz.
    return max(results.values(), key=len)


def _parallel_calls(messages, system, image_b64, max_tokens, temperature, task, has_image):
    providers = _available_providers(task, has_image)
    if not providers:
        raise LLMError("Hech qanday AI API kaliti sozlanmagan.")

    results = {}
    with ThreadPoolExecutor(max_workers=len(providers)) as executor:
        futures = {
            executor.submit(
                _call_one,
                provider,
                messages,
                system,
                image_b64,
                max_tokens,
                temperature,
            ): provider
            for provider in providers
        }

        for future in as_completed(futures):
            provider = futures[future]
            try:
                results[provider] = future.result()
                log.info("%s parallel javob berdi", provider)
            except Exception as exc:
                log.warning("%s parallel xatosi: %s", provider, exc)

    return results


def chat_multi(
    history,
    user_text,
    system,
    image=None,
    max_tokens=None,
    task="chat",
    temperature=None,
):
    system, max_tokens, temperature = _presentation_settings(
        user_text, system, max_tokens, temperature
    )
    messages = list(history or []) + [{"role": "user", "content": user_text}]
    image_b64 = base64.b64encode(image).decode("ascii") if image else None

    results = _parallel_calls(
        messages,
        system,
        image_b64,
        max_tokens,
        temperature,
        task,
        bool(image),
    )
    if not results:
        # Parallel so'rovlar muvaffaqiyatsiz bo'lsa, oddiy fallback.
        return chat(
            history,
            user_text,
            system,
            image=image,
            max_tokens=max_tokens,
            task=task,
            temperature=temperature,
        )

    return _merge_answers(user_text, results)


# ================================================================
# TASNIFLASH
# ================================================================

def _classify_via(provider, text, has_image, has_doc):
    user_content = (
        f"has_image: {str(has_image).lower()}\n"
        f"has_doc: {str(has_doc).lower()}\n"
        f"xabar: {text}"
    )

    if provider in ("groq", "openrouter"):
        is_groq = provider == "groq"
        model = config.CLASSIFIER_MODEL if is_groq else config.OPENROUTER_MODEL
        payload = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": CLASSIFIER_PROMPT},
                {"role": "user", "content": user_content},
            ],
        }
        if is_groq:
            payload["max_completion_tokens"] = 600
            _apply_reasoning(payload)
            url = GROQ_BASE + "/chat/completions"
            headers = {"Authorization": "Bearer " + config.GROQ_API_KEY}
        else:
            payload["max_tokens"] = 600
            url = OPENROUTER_URL
            headers = {
                "Authorization": "Bearer " + config.OPENROUTER_API_KEY,
                "X-Title": "AI Yordamchi",
            }

        resp = _call(
            provider,
            lambda: _session.post(
                url, headers=headers, json=payload,
                timeout=config.REQUEST_TIMEOUT,
            ),
            attempts=2,
        )
        content = _groq_text(resp.json(), provider)

    elif provider == "gemini":
        payload = {
            "system_instruction": {"parts": [{"text": CLASSIFIER_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_content}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 600},
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
        candidates = resp.json().get("candidates") or []
        if not candidates:
            raise LLMError("Gemini tasniflashda bo'sh javob qaytardi.")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        content = "".join(p.get("text", "") for p in parts)
    else:
        raise LLMError(f"Noma'lum provayder: {provider}")

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

    return action, prompt if action in ("draw", "edit") else ""


def classify(text, has_image, has_doc=False):
    providers = _provider_order("chat", False)
    for provider in providers:
        try:
            return _classify_via(provider, text, has_image, has_doc)
        except Exception as exc:
            log.warning("%s tasniflash xatosi: %s", provider, exc)
    return "chat", ""


def classify_vote(text, has_image, has_doc=False):
    providers = _available_providers("chat", False)
    if len(providers) < 2:
        return classify(text, has_image, has_doc)

    votes = []
    with ThreadPoolExecutor(max_workers=len(providers)) as executor:
        futures = {
            executor.submit(
                _classify_via, p, text, has_image, has_doc
            ): p
            for p in providers
        }
        for future in as_completed(futures):
            try:
                votes.append(future.result())
            except Exception as exc:
                log.warning("Tasniflash xatosi: %s", exc)

    if not votes:
        return classify(text, has_image, has_doc)
    if len(votes) == 1:
        return votes[0]

    actions = [action for action, _ in votes]
    action = Counter(actions).most_common(1)[0][0]
    prompts = [prompt for act, prompt in votes if act == action and prompt]
    return action, max(prompts, key=len) if prompts else ""


# ================================================================
# RASM PROMPTI
# ================================================================

def refine_prompt(text):
    """O'zbekcha rasm tavsifini inglizcha promptga aylantiradi."""
    providers = _provider_order("chat", False)
    for provider in providers:
        try:
            answer = _call_one(
                provider,
                [{"role": "user", "content": text}],
                REFINER_PROMPT,
                None,
                500,
                0.2,
            )
            return answer[:600] or text
        except Exception as exc:
            log.warning("%s promptni aniqlashtira olmadi: %s", provider, exc)
    return text


# ================================================================
# OVOZNI MATNGA AYLANTIRISH
# ================================================================

def transcribe(audio, filename="voice.ogg"):
    """Groq Whisper orqali ovozni matnga aylantiradi."""
    if not config.GROQ_API_KEY:
        raise LLMError("Ovozli xabar uchun GROQ_API_KEY kerak.")

    form = {
        "model": config.STT_MODEL,
        "response_format": "json",
        "temperature": "0",
    }
    if config.STT_LANGUAGE:
        form["language"] = config.STT_LANGUAGE

    resp = _call(
        "Groq transcription",
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
    except (ValueError, json.JSONDecodeError) as exc:
        log.warning("Ovoz matniga aylantirish javobi noto'g'ri: %s", exc)
        return ""
