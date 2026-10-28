
"""
AI Telegram Bot
Groq, OpenRouter, Gemini, Brave Search, rasm, hujjat, ovoz va slaydlar.
Mavjud loyiha modullari bilan ishlash uchun yozilgan.
"""

import functools
import html
import logging
import re
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

import config
import docs
import images
import llm
import state
from formatting import split_text, to_telegram_html
from keyboards import MODES, main_menu, modes_menu


# ============================================================
# LOGGING VA BOT
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
log = logging.getLogger("bot")

bot = telebot.TeleBot(
    config.TELEGRAM_TOKEN,
    parse_mode=None,
    threaded=True,
    num_threads=config.WORKERS,
)


# ============================================================
# MATNNI NORMALIZATSIYA QILISH VA SO'ROVNI ANIQLASH
# ============================================================

def _norm(text):
    """Turli apostrof va ortiqcha bo'shliqlarni normallashtiradi."""
    text = str(text or "").lower()
    for char in ("ʻ", "‘", "’", "`", "´", "ʼ"):
        text = text.replace(char, "'")
    return re.sub(r"\s+", " ", text).strip()


_DRAW_HINT = re.compile(
    r"\b(rasm yarat|rasm chiz|surat yarat|chizib ber|"
    r"generate image|create an image|draw|logo yarat)\b",
    re.I,
)
_IMAGE_OBJECT = re.compile(
    r"\b(rasm\w*|surat\w*|foto\w*|image\w*|picture\w*|photo\w*|"
    r"\u0444\u043e\u0442\u043e\w*|\u043a\u0430\u0440\u0442\u0438\u043d\w*|"
    r"\u0438\u0437\u043e\u0431\u0440\u0430\u0436\w*)\b",
    re.I,
)
_IMAGE_ACTION = re.compile(
    r"(qil\w*|chiz\w*|yarat\w*|yasab\s*ber|ber\w*|yubor\w*|"
    r"tashla\w*|ko'rsat\w*|kerak|xohlayman|istayman|"
    r"create|draw|make|generate|send|show|want|need|"
    r"\u0441\u0434\u0435\u043b\w*|\u0441\u043e\u0437\u0434\w*|"
    r"\u043d\u0430\u0440\u0438\u0441\w*|\u0441\u0433\u0435\u043d\u0435\u0440\w*|"
    r"\u043f\u0440\u0438\u0448\u043b\w*|\u043f\u043e\u043a\u0430\u0436\w*|\u0445\u043e\u0447\w*)",
    re.I,
)
_IMAGE_HOWTO = re.compile(
    r"\b(how\s+(?:do\s+i|to)|qanday\s+qilib|\u043a\u0430\u043a\s+(?:\u043c\u043d\u0435|\u043c\u043e\u0436\u043d\u043e))\b",
    re.I,
)
_IMAGE_CORRECTION = re.compile(
    r"\b(emas|xato|noto'g'ri|adash|wrong|instead|incorrect|not|"
    r"\u043d\u0435\u0442|\u043d\u0435\u043f\u0440\u0430\u0432\u0438\u043b\u044c\u043d|\u0432\u043c\u0435\u0441\u0442\u043e)\b",
    re.I,
)
_EDIT_HINT = re.compile(
    r"\b(tahrir|fonini|fonni|rangini|o'zgartir|"
    r"ozgartir|almashtir|olib tashla|qo'shib ber|edit image)\b",
    re.I,
)
_FILE_HINT = re.compile(
    r"\b(fayl|hujjat|docx|word|pdf|excel|xlsx|csv|txt|json|"
    r"jadval|hisobot|rezyume|prezentatsiya|presentation|"
    r"taqdimot|slayd|slide|pptx|powerpoint)\b",
    re.I,
)
_PRESENTATION_TERM = re.compile(
    r"\b(prezentatsiya|taqdimot|\u043f\u0440\u0435\u0437\u0435\u043d\u0442\u0430\u0446\w*|presentation|slides?|slayd\w*|\u0441\u043b\u0430\u0439\u0434\w*|pptx|powerpoint)\b",
    re.I,
)
_PRESENTATION_ACTION = re.compile(
    r"(qilib\s*ber|tayyor\w*|yarat\w*|yasab\s*ber|"
    r"\u0441\u043e\u0437\u0434\w*|\u0441\u0434\u0435\u043b\u0430\w*|\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\w*|\u0441\u0433\u0435\u043d\u0435\u0440\u0438\u0440\w*|"
    r"create|make|prepare|generate|build|\b\d{1,2}\s*(?:ta\s*)?(?:slayd\w*|slides?|\u0441\u043b\u0430\u0439\u0434\w*)\b)",
    re.I,
)
_DOCEDIT_HINT = re.compile(
    r"\b(tahrir|tuzat|qayta yoz|tarjima|qo'sh|o'chir|"
    r"qisqartir|kengaytir|formatla|to'g'rila|to'ldir|"
    r"edit|translate|fix|rewrite|convert)\b",
    re.I,
)
_IMAGE_FOLLOWUP_HINT = re.compile(
    r"\b(shu rasm|bu rasm|oldingi rasm|rasmdagi|suratdagi|"
    r"shu surat|bu surat|rasmda nima|this image|that picture)\b",
    re.I,
)

# Yangi voqealar, jadval, natija, sovrin va joriy holat savollari.
_REALTIME_HINT = re.compile(
    r"(bugun|bugungi|hozir|hozirgi|kecha|ertaga|"
    r"joriy yil|shu yil|real.?vaqt|eng so'nggi|eng sunggi|"
    r"oxirgi yangilik|yangiliklar|qachon|qacon|qach|"
    r"soat nech|natija|hisob|jadval|turnir|"
    r"real madrid|barcelona|barca|el clasico|"
    r"champions league|premier league|la liga|ucl|"
    r"oltin to'p|oltin top|ballon d'or|ballon dor|ballondor|"
    r"golden ball|g'olib|golib|sovrinni kim|kim yutdi|"
    r"transfer|valyuta|dollar kurs|narx|ob.?havo|"
    r"qaysi yil|nechanchi yil|сегодня|сейчас|погода|новости|футбол)",
    re.I,
)


def looks_like_image_request(text, has_image=False):
    text = _norm(text)
    explicit_creation = bool(
        (_DRAW_HINT.search(text) or (_IMAGE_OBJECT.search(text) and _IMAGE_ACTION.search(text)))
        and not _IMAGE_HOWTO.search(text)
    )
    return bool(explicit_creation or (has_image and _EDIT_HINT.search(text)))


_EXPLICIT_ART_STYLE = re.compile(
    r"\b(illustration|cartoon|anime|watercolor|painting|sketch|vector|logo|icon|"
    r"chizilgan|multfilm|animatsion|akvarel|illyustratsiya)\b",
    re.I,
)


def _refine_image_prompt(text):
    """Prefer a lifelike photo unless the user explicitly asks for art/design."""
    prompt = llm.refine_prompt(text) or str(text or "").strip()
    if not _EXPLICIT_ART_STYLE.search(text or ""):
        prompt += (
            ". Photorealistic real-life photograph, natural anatomy and textures, "
            "realistic environment and lighting. No text, captions, watermark, "
            "ASCII art, or illustration."
        )
    return prompt[:1800]


def _refine_image_correction(previous_prompt, correction):
    """Make the latest correction override the subject of the prior image."""
    normalized = _norm(correction)
    match = re.search(r"(.{1,100}?)\bemas\b(.{1,100})", normalized, re.I)
    if match:
        rejected = " ".join(match.group(1).split()[-4:]).strip(" ,.!?:;")
        desired = re.split(
            r"\b(deb|bo'lsin|qaytib|yozsam|qil|qilib|ber|iltimos)\b",
            match.group(2),
            maxsplit=1,
        )[0].strip(" ,.!?:;")
        instruction = (
            f"The previous request was: {previous_prompt}. "
            f"The user's latest correction is: {correction}. "
            f"The rejected subject is {rejected}. The desired subject is {desired}. "
            f"Depict only {desired}; do not depict {rejected}. The latest correction "
            "overrides the earlier result. Make a photorealistic real-life photograph."
        )
    else:
        instruction = (
            f"Previous image request: {previous_prompt}. Latest user correction: "
            f"{correction}. Follow the correction as authoritative, replace the "
            "mistaken subject, and create a photorealistic real-life photograph."
        )
    return _refine_image_prompt(instruction)


def is_presentation_request(text):
    """Detect presentation creation requests before the general AI classifier."""
    normalized = _norm(text)
    return bool(
        _PRESENTATION_TERM.search(normalized)
        and _PRESENTATION_ACTION.search(normalized)
    )


# ============================================================
# XABAR YUBORISH
# ============================================================

WELCOME = (
    "👋 <b>Salom, {name}!</b>\n\n"
    "Men AI yordamchisiman.\n"
    "💬 Savollarga javob beraman.\n"
    "🌐 Kerak bo'lsa internetdan qidiraman.\n"
    "🖼 Rasmlarni tahlil qilaman.\n"
    "🎨 Rasm yaratish va tahrirlashga yordam beraman.\n"
    "📄 Hujjat va taqdimotlar tayyorlayman.\n"
    "🎤 Ovozli xabarlarni matnga aylantiraman.\n\n"
    "/help — barcha buyruqlar"
)

HELP = (
    "ℹ️ <b>BOT YORDAMI</b>\n\n"
    "/start — botni boshlash\n"
    "/help — yordam\n"
    "/mode — javob rejimini tanlash\n"
    "/clear — suhbat va vaqtinchalik hujjat kontekstini tozalash\n"
    "/status — sozlamalar va limitlar\n"
    "/id — Telegram ID\n"
    "/qidir so'rov — internetdan qidirish\n"
    "/rasm tavsif — rasm yaratish\n"
    "/tahrir ko'rsatma — oxirgi rasmni tahrirlash\n"
    "/fayl ko'rsatma — fayl yoki taqdimot yaratish\n\n"
    "PDF, Word, Excel va PowerPoint hujjatlarini yuborishingiz mumkin.\n"
    "Muhim faktlarni, ayniqsa yangilik va sanalarni, manbalardan tekshiring."
)


def _send(chat_id, text, reply_to=None, **kwargs):
    try:
        return bot.send_message(
            chat_id,
            text,
            reply_to_message_id=reply_to,
            **kwargs,
        )
    except ApiTelegramException as exc:
        if reply_to and "reply" in str(exc).lower():
            return bot.send_message(chat_id, text, **kwargs)
        raise


def reply(message, text, **kwargs):
    return _send(message.chat.id, text, message.message_id, **kwargs)


def safe_reply(message, text):
    try:
        reply(message, text)
    except Exception:
        log.exception("Xato xabarini yuborib bo'lmadi")


def send_long(message, text):
    """Uzun javobni Telegram xabar chegarasiga mos bo'lib yuboradi."""
    chunks = split_text(str(text or ""), config.TELEGRAM_MAX_CHARS)

    for index, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        reply_to = message.message_id if index == 0 else None
        try:
            _send(
                message.chat.id,
                to_telegram_html(chunk),
                reply_to,
                parse_mode="HTML",
            )
        except ApiTelegramException:
            _send(message.chat.id, chunk, reply_to)


def send_photo(message, data):
    try:
        bot.send_photo(
            message.chat.id,
            data,
            reply_to_message_id=message.message_id,
        )
    except ApiTelegramException as exc:
        if "reply" in str(exc).lower():
            bot.send_photo(message.chat.id, data)
        else:
            raise


def send_document(message, name, data, caption=""):
    import io

    file_obj = io.BytesIO(data)
    file_obj.name = name
    try:
        bot.send_document(
            message.chat.id,
            file_obj,
            caption=(caption[:1000] or None),
            reply_to_message_id=message.message_id,
        )
    except ApiTelegramException as exc:
        if "reply" not in str(exc).lower():
            raise
        file_obj = io.BytesIO(data)
        file_obj.name = name
        bot.send_document(
            message.chat.id,
            file_obj,
            caption=(caption[:1000] or None),
        )


def is_allowed(user_id):
    return not config.ALLOWED_USERS or user_id in config.ALLOWED_USERS


def clear_all(user_id):
    state.clear_history(user_id)
    docs.clear(user_id)


def protected(func):
    """Ruxsat, rate limit va xatolarni umumiy boshqaradi."""
    @functools.wraps(func)
    def wrapper(message, *args, **kwargs):
        if not message.from_user:
            return

        user_id = message.from_user.id
        if not is_allowed(user_id):
            safe_reply(message, f"⛔ Ruxsat yo'q. Telegram ID: {user_id}")
            return

        wait = state.rate_wait(user_id)
        if wait > 0:
            safe_reply(message, f"⏳ {wait:.0f} soniya kutib qayta urinib ko'ring.")
            return

        with state.user_slot(user_id) as acquired:
            if not acquired:
                safe_reply(message, "⏳ Oldingi so'rovingiz hali bajarilmoqda.")
                return
            try:
                return func(message, *args, **kwargs)
            except (llm.LLMError, images.ImageError, docs.DocError) as exc:
                log.warning("So'rov xatosi: %s", type(exc).__name__, exc_info=True)
                safe_reply(
                    message,
                    "⚠️ So'rov bajarilmadi: "
                    + llm._redact_secrets(exc)[:350],
                )
            except ApiTelegramException:
                log.exception("Telegram API xatosi")
                safe_reply(message, "⚠️ Telegram bilan aloqa xatosi. Keyinroq urinib ko'ring.")
            except Exception:
                log.exception("Handlerda kutilmagan xato")
                safe_reply(message, "❌ Kutilmagan xato yuz berdi. Keyinroq qayta urinib ko'ring.")
    return wrapper


# ============================================================
# AI CHAT VA QIDIRUV
# ============================================================

def do_chat(message, text, image=None, label=None):
    user_id = message.from_user.id
    bot.send_chat_action(message.chat.id, "typing")

    system = MODES[state.get_mode(user_id)][1]

    # Yuklangan hujjatni faqat unga aloqador savolda qo'shamiz.
    uploaded_doc = docs.get(user_id)
    if uploaded_doc and (
        _DOCEDIT_HINT.search(_norm(text))
        or re.search(r"\b(fayl|hujjat|shu hujjat|bu hujjat|undagi|mazmuni)\b", _norm(text))
    ):
        system += docs.context_block(uploaded_doc)

    history = state.get_history(user_id)

    if image is None and _REALTIME_HINT.search(_norm(text)):
        # Muhim: qidiruv ishlamasa, oddiy AI'ga jim o'tib ketmaydi.
        answer = llm.chat_with_search(history, text, system)
    else:
        answer = llm.chat(history, text, system, image=image)

    state.remember(user_id, label or text, answer)
    send_long(message, answer)


# ============================================================
# RASM
# ============================================================

def check_image_quota(message):
    if not config.IMAGES_ENABLED:
        reply(message, "🎨 Rasm xizmati sozlanmagan.")
        return False
    if state.images_left(message.from_user.id) <= 0:
        reply(message, f"🎨 Bugungi rasm limiti ({config.IMAGE_DAILY_LIMIT}) tugadi.")
        return False
    return True


def deliver_image(message, data, note):
    if not data:
        raise images.ImageError("Rasm xizmati bo'sh natija qaytardi.")

    user_id = message.from_user.id
    try:
        state.set_image(user_id, images.shrink(data))
    except Exception:
        log.warning("Rasm keshga saqlanmadi", exc_info=True)
    send_photo(message, data)
    state.set_image_prompt(user_id, note)
    state.count_image(user_id)
    state.remember(user_id, note, "[Rasm yuborildi]")


def do_draw(message, prompt, note):
    if not check_image_quota(message):
        return
    bot.send_chat_action(message.chat.id, "upload_photo")
    deliver_image(message, images.make_image(prompt), note)


def do_edit(message, image_bytes, prompt, note):
    if image_bytes is None:
        reply(message, "✏️ Avval rasm yuboring, keyin tahrirlashni so'rang.")
        return
    if not check_image_quota(message):
        return
    bot.send_chat_action(message.chat.id, "upload_photo")
    deliver_image(message, images.edit_image(image_bytes, prompt), note)


def handle_image(message, file_id, caption):
    try:
        info = bot.get_file(file_id)
        raw = bot.download_file(info.file_path)
        data = images.shrink(raw)
    except Exception:
        log.exception("Rasmni yuklash/o'qish xatosi")
        reply(message, "🖼 Rasmni o'qib bo'lmadi. JPG yoki PNG yuborib ko'ring.")
        return

    state.set_image(message.from_user.id, data)
    state.set_image_prompt(message.from_user.id, None)
    caption = (caption or "").strip()

    if caption and config.IMAGES_ENABLED and _EDIT_HINT.search(_norm(caption)):
        do_edit(message, data, llm.refine_prompt(caption), caption)
        return

    question = caption or "Bu rasmda nima bor? Batafsil tushuntir."
    do_chat(message, question, image=data, label=f"[Rasm yuborildi] {question}")


# ============================================================
# FAYL / TAQDIMOT
# ============================================================

def do_file(message, instruction):
    user_id = message.from_user.id

    if config.FILE_DAILY_LIMIT > 0 and state.files_left(user_id) <= 0:
        reply(message, f"📄 Bugungi fayl limiti ({config.FILE_DAILY_LIMIT}) tugadi.")
        return

    bot.send_chat_action(message.chat.id, "upload_document")
    image_limit = state.images_left(user_id) if config.IMAGES_ENABLED else 0
    allow_images = config.IMAGES_ENABLED and image_limit > 0

    result = docs.generate(
        state.get_history(user_id),
        instruction,
        docs.get(user_id),
        allow_images=allow_images,
        image_limit=image_limit,
    )

    for _ in range(getattr(result, "images_added", 0)):
        state.count_image(user_id)

    docs.store(user_id, result.name, result.text)
    send_document(message, result.name, result.data, result.summary)
    state.count_file(user_id)
    state.remember(
        user_id,
        instruction,
        f"[Fayl yuborildi: {result.name}] {result.summary}",
    )


def route_text(message, text):
    user_id = message.from_user.id
    normalized = _norm(text)
    last_image = state.get_image(user_id) if config.IMAGES_ENABLED else None
    uploaded_doc = docs.get(user_id)

    # Presentation creation is deterministic: do not let the general intent
    # classifier redirect a clear PPT request to ordinary chat.
    if is_presentation_request(normalized):
        do_file(message, text)
        return

    previous_image_prompt = (
        state.get_image_prompt(user_id) if config.IMAGES_ENABLED else None
    )
    if (
        last_image
        and previous_image_prompt
        and _IMAGE_CORRECTION.search(normalized)
    ):
        if not config.IMAGES_ENABLED:
            reply(message, "Rasm yaratish xizmati hozir sozlanmagan.")
        else:
            do_draw(
                message,
                _refine_image_correction(previous_image_prompt, text),
                text,
            )
        return

    wants_image = looks_like_image_request(text, last_image is not None)
    if wants_image:
        if not config.IMAGES_ENABLED:
            reply(message, "Rasm yaratish xizmati hozir sozlanmagan.")
            return
        if last_image and _EDIT_HINT.search(normalized):
            do_edit(message, last_image, llm.refine_prompt(text), text)
        else:
            do_draw(message, _refine_image_prompt(text), text)
        return

    wants_file = bool(_FILE_HINT.search(normalized) or (uploaded_doc and _DOCEDIT_HINT.search(normalized)))

    action, prompt = "chat", ""
    if wants_file:
        try:
            action, prompt = llm.classify_vote(text, last_image is not None, uploaded_doc is not None)
        except Exception:
            log.exception("So'rovni tasniflash xatosi")
            if wants_image:
                action = "edit" if last_image and _EDIT_HINT.search(normalized) else "draw"
            elif wants_file:
                action = "file"

    if action == "chat" and last_image and _IMAGE_FOLLOWUP_HINT.search(normalized) and not wants_file:
        do_chat(message, text, image=last_image)
    elif action == "draw" and config.IMAGES_ENABLED:
        do_draw(message, _refine_image_prompt(prompt or text), text)
    elif action == "edit" and config.IMAGES_ENABLED:
        do_edit(message, last_image, llm.refine_prompt(prompt or text), text)
    elif action == "file":
        do_file(message, text)
    else:
        do_chat(message, text)


# ============================================================
# BUYRUQLAR
# ============================================================

@bot.message_handler(commands=["start"], chat_types=["private"])
@protected
def cmd_start(message):
    name = html.escape(message.from_user.first_name or "do'st")
    reply(message, WELCOME.format(name=name), parse_mode="HTML", reply_markup=main_menu())


@bot.message_handler(commands=["help"], chat_types=["private"])
@protected
def cmd_help(message):
    reply(message, HELP, parse_mode="HTML", reply_markup=main_menu())


@bot.message_handler(commands=["id"], chat_types=["private"])
@protected
def cmd_id(message):
    reply(message, f"🆔 Telegram ID: {message.from_user.id}")


@bot.message_handler(commands=["clear"], chat_types=["private"])
@protected
def cmd_clear(message):
    clear_all(message.from_user.id)
    reply(message, "🧹 Suhbat tarixi va vaqtinchalik hujjat konteksti tozalandi.")


@bot.message_handler(commands=["mode"], chat_types=["private"])
@protected
def cmd_mode(message):
    reply(message, "🎭 Javob rejimini tanlang:", reply_markup=modes_menu(state.get_mode(message.from_user.id)))


@bot.message_handler(commands=["rasm"], chat_types=["private"])
@protected
def cmd_draw(message):
    prompt = (message.text or "").partition(" ")[2].strip()
    if not prompt:
        reply(message, "Tavsif yozing:\n/rasm quyosh botayotgan dengiz")
        return
    do_draw(message, _refine_image_prompt(prompt), prompt)


@bot.message_handler(commands=["tahrir"], chat_types=["private"])
@protected
def cmd_edit(message):
    prompt = (message.text or "").partition(" ")[2].strip()
    if not prompt:
        reply(message, "Ko'rsatma yozing:\n/tahrir fonni ko'k rangga o'zgartir")
        return
    do_edit(message, state.get_image(message.from_user.id), llm.refine_prompt(prompt), prompt)


@bot.message_handler(commands=["fayl"], chat_types=["private"])
@protected
def cmd_file(message):
    instruction = (message.text or "").partition(" ")[2].strip()
    if not instruction:
        reply(
            message,
            "Fayl turini va mavzusini yozing:\n"
            "/fayl Sun'iy intellekt haqida 8 slaydli taqdimot\n"
            "/fayl Mahsulotlar uchun Excel jadvali\n"
            "/fayl Ekologiya haqida Word hujjati",
        )
        return
    do_file(message, instruction)


@bot.message_handler(commands=["status"], chat_types=["private"])
@protected
def cmd_status(message):
    user_id = message.from_user.id
    providers = []
    if config.GROQ_API_KEY:
        providers.append("Groq")
    if config.OPENROUTER_API_KEY:
        providers.append("OpenRouter")
    if config.GEMINI_API_KEY:
        providers.append("Gemini")

    text = (
        "🤖 <b>BOT HOLATI</b>\n\n"
        f"🧠 AI: {html.escape(', '.join(providers) or 'Kalit topilmadi')}\n"
        f"🌐 Brave Search: {'Sozlangan' if config.BRAVE_SEARCH_ENABLED else 'Kalit topilmadi'}\n"
        f"🎨 Rasm: {'Yoqilgan' if config.IMAGES_ENABLED else 'O‘chiq'}\n"
        f"📊 Presenton: {'Yoqilgan' if config.PRESENTON_ENABLED else 'Ichki generator'}\n"
        f"🎭 Rejim: {html.escape(MODES[state.get_mode(user_id)][0])}\n"
        f"🖼 Qolgan rasm limiti: {state.images_left(user_id)}\n"
        f"📄 Qolgan fayl limiti: {state.files_left(user_id)}\n"
        f"💬 Xotiradagi xabarlar: {len(state.get_history(user_id))}\n\n"
        "Bu tekshiruv kalitlar sozlanganini ko'rsatadi; API ishlashini to'liq kafolatlamaydi."
    )
    reply(message, text, parse_mode="HTML")


@bot.message_handler(commands=["qidir"], chat_types=["private"])
@protected
def cmd_search(message):
    query = (message.text or "").partition(" ")[2].strip()
    if not query:
        reply(message, "So'rov yozing:\n/qidir sun'iy intellekt yangiliklari")
        return
    if not config.BRAVE_SEARCH_ENABLED:
        reply(message, "🌐 BRAVE_API_KEY sozlanmagan. Northflank Environment Variables'ni tekshiring.")
        return

    bot.send_chat_action(message.chat.id, "typing")
    results = llm.web_search(query, max_results=5)
    if not results:
        reply(message, "🔎 Ishonchli qidiruv natijasi olinmadi. API kaliti, kvota yoki internetni tekshiring.")
        return

    system = MODES[state.get_mode(message.from_user.id)][1]
    answer = llm.chat_with_search(
        state.get_history(message.from_user.id), query, system,
        search_results=results,
    )
    state.remember(message.from_user.id, f"[Internet qidiruvi] {query}", answer)
    send_long(message, answer)


# ============================================================
# TUGMALAR
# ============================================================

def edit_message(chat_id, message_id, text, markup):
    try:
        bot.edit_message_text(text, chat_id, message_id, parse_mode="HTML", reply_markup=markup)
    except ApiTelegramException as exc:
        if "not modified" not in str(exc).lower():
            raise


@bot.callback_query_handler(func=lambda call: True)
def on_callback(call):
    toast = None
    try:
        if not is_allowed(call.from_user.id):
            toast = "⛔ Ruxsat yo'q"
        elif call.message:
            user_id = call.from_user.id
            chat_id, message_id = call.message.chat.id, call.message.message_id
            data = call.data or ""

            if data == "menu:modes":
                edit_message(chat_id, message_id, "🎭 <b>Javob rejimini tanlang:</b>", modes_menu(state.get_mode(user_id)))
            elif data.startswith("mode:"):
                key = data.split(":", 1)[1]
                if key in MODES:
                    state.set_mode(user_id, key)
                    try:
                        bot.edit_message_reply_markup(chat_id, message_id, reply_markup=modes_menu(key))
                    except ApiTelegramException as exc:
                        if "not modified" not in str(exc).lower():
                            raise
                    toast = f"Rejim: {MODES[key][0]}"
            elif data == "menu:clear":
                clear_all(user_id)
                toast = "🧹 Suhbat tozalandi"
            elif data == "menu:help":
                edit_message(chat_id, message_id, HELP, main_menu())
            elif data == "menu:back":
                name = html.escape(call.from_user.first_name or "do'st")
                edit_message(chat_id, message_id, WELCOME.format(name=name), main_menu())
    except Exception:
        log.exception("Callback xatosi")
        toast = "Xatolik yuz berdi"
    finally:
        try:
            bot.answer_callback_query(call.id, text=toast)
        except Exception:
            pass


# ============================================================
# MATN VA MEDIA HANDLERLARI
# ============================================================

@bot.message_handler(content_types=["text"], func=lambda m: not (m.text or "").startswith("/"), chat_types=["private"])
@protected
def on_text(message):
    route_text(message, message.text or "")


@bot.message_handler(content_types=["photo"], chat_types=["private"])
@protected
def on_photo(message):
    handle_image(message, message.photo[-1].file_id, message.caption)


@bot.message_handler(
    content_types=["document"],
    func=lambda m: bool(m.document and (m.document.mime_type or "").startswith("image/")),
    chat_types=["private"],
)
@protected
def on_image_document(message):
    if (message.document.file_size or 0) > 10 * 1024 * 1024:
        reply(message, "🖼 Rasm juda katta. 10 MB dan kichik rasm yuboring.")
        return
    handle_image(message, message.document.file_id, message.caption)


@bot.message_handler(content_types=["document"], chat_types=["private"])
@protected
def on_file_document(message):
    document = message.document
    if (document.file_size or 0) > docs.MAX_FILE_BYTES:
        reply(message, f"📄 Fayl juda katta. Maksimal hajm: {docs.MAX_FILE_BYTES // (1024 * 1024)} MB.")
        return

    name = document.file_name or "fayl"
    mime_type = document.mime_type or ""
    docs.precheck(name, mime_type)

    bot.send_chat_action(message.chat.id, "typing")
    info = bot.get_file(document.file_id)
    raw = bot.download_file(info.file_path)
    text, truncated = docs.extract(name, raw, mime_type)
    docs.store(message.from_user.id, name, text, truncated)

    if truncated:
        reply(message, f"📄 «{name}» katta fayl. Dastlabki taxminan {docs.MAX_TEXT_CHARS} belgisi o'qildi.")

    caption = (message.caption or "").strip()
    if not caption:
        do_chat(
            message,
            f"Men «{name}» faylini yubordim. Mazmunini 3–5 gapda tushuntir va asosiy fikrlarini sanab ber.",
            label=f"[Fayl yuklandi] {name}",
        )
        return

    action, _ = llm.classify_vote(caption, False, True)
    if action == "file":
        do_file(message, caption)
    else:
        do_chat(message, caption, label=f"[Fayl: {name}] {caption}")


@bot.message_handler(content_types=["voice"], chat_types=["private"])
@protected
def on_voice(message):
    voice = message.voice
    if (voice.duration or 0) > config.VOICE_MAX_SECONDS:
        reply(message, f"🎤 Ovozli xabar uzun. Maksimal davomiylik: {config.VOICE_MAX_SECONDS} soniya.")
        return

    bot.send_chat_action(message.chat.id, "typing")
    info = bot.get_file(voice.file_id)
    audio = bot.download_file(info.file_path)
    text = llm.transcribe(audio, filename="voice.ogg")
    if not text:
        reply(message, "🎤 Ovozli xabar matnga aylantirilmadi.")
        return

    reply(message, "🎤 <b>Tushunilgan matn:</b>\n" + html.escape(text[:1000]), parse_mode="HTML")
    route_text(message, text)


@bot.message_handler(
    content_types=["video", "audio", "sticker", "video_note", "animation", "location", "contact"],
    chat_types=["private"],
)
def on_other(message):
    if message.from_user and is_allowed(message.from_user.id):
        reply(message, "📝 Hozircha matn, rasm, ovozli xabar va hujjatlarni qayta ishlay olaman.")


# ============================================================
# NORTHFLANK HEALTH-CHECK
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        return


def start_health_server():
    port = config.HEALTH_PORT
    if not port:
        return
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    except OSError:
        log.exception("Health-check server ishga tushmadi")
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Health-check %s-portda ishlayapti", port)


def shutdown(signum, _frame):
    log.info("Signal %s: bot to'xtatilmoqda", signum)
    try:
        bot.stop_polling()
    except Exception:
        log.exception("Pollingni to'xtatish xatosi")


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    if config.APP_PASSWORD:
        # Parol bilan himoyalangan web ilovasi health portda xizmat qiladi.
        # Parol bo'lmasa, eski yengil health-check serveri ishlayveradi.
        import web
        web.start(config.HEALTH_PORT)
    else:
        start_health_server()

    try:
        bot.remove_webhook()
    except Exception:
        log.warning("Webhookni olib tashlash muvaffaqiyatsiz bo'ldi", exc_info=True)

    me = bot.get_me()
    commands = [
        types.BotCommand("start", "Botni boshlash"),
        types.BotCommand("help", "Yordam"),
        types.BotCommand("mode", "Javob rejimi"),
        types.BotCommand("clear", "Suhbatni tozalash"),
        types.BotCommand("status", "Bot holati"),
        types.BotCommand("qidir", "Internetdan qidirish"),
        types.BotCommand("rasm", "Rasm yaratish"),
        types.BotCommand("tahrir", "Rasmni tahrirlash"),
        types.BotCommand("fayl", "Fayl yoki taqdimot yaratish"),
        types.BotCommand("id", "Telegram ID"),
    ]
    try:
        bot.set_my_commands(commands)
    except Exception:
        log.warning("Buyruqlarni o'rnatib bo'lmadi", exc_info=True)

    log.info(
        "Bot ishga tushdi | @%s | Brave=%s | rasm=%s | web=%s",
        me.username,
        "yoqilgan" if config.BRAVE_SEARCH_ENABLED else "kalit yo'q",
        "yoqilgan" if config.IMAGES_ENABLED else "o'chiq",
        "yoqilgan" if config.APP_PASSWORD else "o'chiq",
    )
    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
