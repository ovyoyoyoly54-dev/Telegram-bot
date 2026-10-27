
"""
AI Telegram Bot — asosiy ishga tushirish fayli.

Imkoniyatlar:
- Groq, OpenRouter va Gemini orqali AI suhbat
- Brave Search orqali internet qidiruvi
- Suhbat tarixini saqlash va tozalash
- Rasmni tahlil qilish, yaratish va tahrirlash
- Hujjatlarni o'qish va yangi fayllar yaratish
- Ovozli xabarlarni matnga aylantirish
- Foydalanuvchi ruxsatlari va limitlari
- Northflank uchun health-check
"""

import functools
import html
import io
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
# FOYDALANUVCHIGA KO'RINADIGAN MATNLAR
# ============================================================

WELCOME = (
    "👋 <b>Salom, {name}!</b>\n\n"
    "Men sun'iy intellekt yordamchisiman. 🤖\n\n"
    "💬 Savollaringizga javob beraman.\n"
    "🌐 Kerak bo'lsa, internetdan ma'lumot qidiraman.\n"
    "🖼 Rasmlarni tahlil qilaman.\n"
    "🎨 Rasm yaratish va tahrirlashga yordam beraman.\n"
    "📄 Hujjatlarni o'qish va yaratishni qo'llab-quvvatlayman.\n"
    "📊 PowerPoint taqdimotlari va jadvallar tayyorlayman.\n"
    "🎤 Ovozli xabarni matnga aylantira olaman.\n\n"
    "Barcha buyruqlarni ko'rish uchun /help ni yuboring."
)

HELP = (
    "ℹ️ <b>BOT YORDAMI</b>\n\n"
    "💬 <b>Oddiy suhbat</b>\n"
    "Savolingizni yozing. Bot mavjud suhbat tarixidan foydalanadi.\n\n"
    "🧠 <b>Buyruqlar</b>\n"
    "/start — botni boshlash\n"
    "/help — yordam\n"
    "/mode — javob rejimini tanlash\n"
    "/clear — suhbat tarixini tozalash\n"
    "/status — sozlamalar va limitlar holati\n"
    "/id — Telegram ID\n"
    "/qidir so'rov — internetdan qidirish\n\n"
    "🎨 <b>Rasmlar</b>\n"
    "/rasm tavsif — yangi rasm yaratish\n"
    "/tahrir ko'rsatma — oxirgi rasmni tahrirlash\n"
    "Rasm yuborib, savolingizni ham yozishingiz mumkin.\n\n"
    "📄 <b>Fayllar</b>\n"
    "/fayl ko'rsatma — hujjat yoki taqdimot yaratish\n"
    "PDF, Word, Excel, PowerPoint va qo'llab-quvvatlanadigan "
    "boshqa fayllarni yuborishingiz mumkin.\n\n"
    "📌 <b>Misollar</b>\n"
    "/qidir sun'iy intellekt bo'yicha yangi yangiliklar\n"
    "/fayl ekologiya haqida 8 slaydli taqdimot\n"
    "/fayl mahsulotlar uchun Excel jadvali\n\n"
    "Eslatma: AI javoblarini muhim holatlarda tekshirib oling."
)


# ============================================================
# MATNNI ANIQLASH
# ============================================================

def _norm(text: str) -> str:
    """Turli apostrof belgilarini bir xil ko'rinishga keltiradi."""
    if not text:
        return ""

    return (
        text.replace("ʻ", "'")
        .replace("‘", "'")
        .replace("’", "'")
        .replace("`", "'")
    )


_DRAW_HINT = re.compile(
    r"(rasm|surat|chiz|tasvir|illyustr|draw|paint|sketch|"
    r"image|picture|logo|poster|generate image|create image)",
    re.I,
)

_EDIT_HINT = re.compile(
    r"(fon|rang|o'zgartir|zgartir|qo'sh|tashla|almashtir|"
    r"edit|background|remove|filter|uslub|qora-oq)",
    re.I,
)

_FILE_HINT = re.compile(
    r"(fayl|hujjat|docx|word|pdf|excel|xlsx|csv|txt|json|"
    r"jadval|rezyume|resume|hisobot|prezentatsiya|presentation|"
    r"taqdimot|slayd|slide|pptx|powerpoint)",
    re.I,
)

_REALTIME_HINT = re.compile(
    r"(bugun|bugungi|hozir|hozirgi|kecha|ertaga|joriy yil|"
    r"shu yil|real.?vaqt|yangi|oxirgi|eng so'nggi|kurs|"
    r"ob.?havo|narx|natija|futbol|football|soccer|"
    r"premier league|la liga|champions league|ucl|transfer|"
    r"yangilik|voqea|valyuta|qachon|soat necha|"
    r"сегодня|сейчас|погода|новости|футбол)",
    re.I,
)

_DOCEDIT_HINT = re.compile(
    r"(tahrir|tuzat|qayta yoz|o'zgartir|tarjima|qo'sh|"
    r"o'chir|qisqartir|kengaytir|formatla|to'g'rila|"
    r"to'ldir|edit|translate|fix|rewrite|convert)",
    re.I,
)

_IMAGE_FOLLOWUP_HINT = re.compile(
    r"(shu rasm|bu rasm|oldingi rasm|rasmdagi|suratdagi|"
    r"shu surat|bu surat|unda nima|rasmda nima|"
    r"this image|that image|this picture|describe it)",
    re.I,
)


def looks_like_image_request(text: str, has_image: bool) -> bool:
    text = _norm(text)
    return bool(
        _DRAW_HINT.search(text)
        or (has_image and _EDIT_HINT.search(text))
    )


# ============================================================
# XABAR YUBORISH YORDAMCHILARI
# ============================================================

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
    return _send(
        message.chat.id,
        text,
        message.message_id,
        **kwargs,
    )


def _safe_reply(message, text):
    try:
        reply(message, text)
    except Exception:
        log.exception("Xato xabarini foydalanuvchiga yuborib bo'lmadi")


def send_long(message, text):
    """Uzun javoblarni Telegram chegarasiga moslab yuboradi."""
    chunks = split_text(text or "", config.TELEGRAM_MAX_CHARS)

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
            # HTML noto'g'ri bo'lsa, oddiy matn bilan qayta urinadi.
            _send(message.chat.id, chunk, reply_to)


def send_photo(message, image_bytes):
    try:
        bot.send_photo(
            message.chat.id,
            image_bytes,
            reply_to_message_id=message.message_id,
        )
    except ApiTelegramException as exc:
        if "reply" in str(exc).lower():
            bot.send_photo(message.chat.id, image_bytes)
        else:
            raise


def send_document(message, name, data, caption=""):
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


def welcome_text(user):
    name = html.escape(user.first_name or "do'st")
    return WELCOME.format(name=name)


# ============================================================
# RUXSATLAR VA UMUMIY XATOLAR
# ============================================================

def is_allowed(user_id):
    return (
        not config.ALLOWED_USERS
        or user_id in config.ALLOWED_USERS
    )


def clear_all(user_id):
    state.clear_history(user_id)
    docs.clear(user_id)


def protected(func):
    """Xabar handlerlari uchun ruxsat, navbat va xato nazorati."""

    @functools.wraps(func)
    def wrapper(message, *args, **kwargs):
        if not message.from_user:
            return

        user_id = message.from_user.id

        if not is_allowed(user_id):
            _safe_reply(
                message,
                "⛔ Botdan foydalanishga ruxsat yo'q.\n"
                f"Telegram ID: {user_id}",
            )
            return

        wait = state.rate_wait(user_id)
        if wait > 0:
            _safe_reply(
                message,
                f"⏳ Iltimos, {wait:.0f} soniya kutib qayta urinib ko'ring.",
            )
            return

        with state.user_slot(user_id) as acquired:
            if not acquired:
                _safe_reply(
                    message,
                    "⏳ Oldingi so'rovingiz hali bajarilmoqda.",
                )
                return

            try:
                return func(message, *args, **kwargs)

            except (llm.LLMError, images.ImageError, docs.DocError) as exc:
                log.warning(
                    "So'rov bajarilmadi: %s",
                    type(exc).__name__,
                    exc_info=True,
                )
                _safe_reply(
                    message,
                    "⚠️ So'rovni bajarib bo'lmadi.\n"
                    f"Sabab: {str(exc)[:400]}",
                )

            except ApiTelegramException:
                log.exception("Telegram API xatosi")
                _safe_reply(
                    message,
                    "⚠️ Telegram bilan aloqa vaqtincha ishlamadi. "
                    "Birozdan keyin qayta urinib ko'ring.",
                )

            except Exception:
                log.exception("Kutilmagan xatolik")
                _safe_reply(
                    message,
                    "❌ Kutilmagan xatolik yuz berdi. "
                    "Xato qayd etildi, keyinroq qayta urinib ko'ring.",
                )

    return wrapper


# ============================================================
# AI SUHBAT VA INTERNET QIDIRUVI
# ============================================================

def do_chat(message, text, image=None, label=None):
    user_id = message.from_user.id
    bot.send_chat_action(message.chat.id, "typing")

    system = MODES[state.get_mode(user_id)][1]

    uploaded_doc = docs.get(user_id)
    if uploaded_doc:
        system += docs.context_block(uploaded_doc)

    history = state.get_history(user_id)

    if image is None and _REALTIME_HINT.search(_norm(text)):
        try:
            answer = llm.chat_with_search(history, text, system)
        except Exception:
            log.warning(
                "Internet qidiruvi muvaffaqiyatsiz; oddiy AI javobiga o'tilmoqda",
                exc_info=True,
            )
            answer = llm.chat_multi(history, text, system)
    else:
        try:
            answer = llm.chat_multi(
                history,
                text,
                system,
                image=image,
            )
        except Exception:
            log.warning(
                "Ko'p provayderli chat xatosi; zaxira usuli sinab ko'riladi",
                exc_info=True,
            )
            answer = llm.chat(
                history,
                text,
                system,
                image=image,
            )

    state.remember(user_id, label or text, answer)
    send_long(message, answer)


# ============================================================
# RASM YARATISH VA TAHRIRLASH
# ============================================================

def _check_image_quota(message):
    if not config.IMAGES_ENABLED:
        reply(message, "🎨 Rasm xizmati hozircha sozlanmagan.")
        return False

    if state.images_left(message.from_user.id) <= 0:
        reply(
            message,
            f"🎨 Bugungi rasm limiti ({config.IMAGE_DAILY_LIMIT} ta) tugadi.",
        )
        return False

    return True


def _deliver_image(message, image_bytes, note):
    if not image_bytes:
        raise images.ImageError("Rasm xizmati bo'sh natija qaytardi.")

    user_id = message.from_user.id

    try:
        state.set_image(user_id, images.shrink(image_bytes))
    except Exception:
        log.warning("Rasm xotiraga saqlanmadi", exc_info=True)

    send_photo(message, image_bytes)
    state.count_image(user_id)
    state.remember(user_id, note, "[Rasm yuborildi]")


def do_draw(message, prompt, note):
    if not _check_image_quota(message):
        return

    bot.send_chat_action(message.chat.id, "upload_photo")
    result = images.make_image(prompt)
    _deliver_image(message, result, note)


def do_edit(message, image_bytes, prompt, note):
    if image_bytes is None:
        reply(message, "✏️ Avval rasm yuboring, keyin tahrirlashni so'rang.")
        return

    if not _check_image_quota(message):
        return

    bot.send_chat_action(message.chat.id, "upload_photo")
    result = images.edit_image(image_bytes, prompt)
    _deliver_image(message, result, note)


# ============================================================
# FAYL VA TAQDIMOT YARATISH
# ============================================================

def do_file(message, instruction):
    user_id = message.from_user.id

    if (
        config.FILE_DAILY_LIMIT > 0
        and state.files_left(user_id) <= 0
    ):
        reply(
            message,
            f"📄 Bugungi fayl limiti ({config.FILE_DAILY_LIMIT} ta) tugadi.",
        )
        return

    bot.send_chat_action(message.chat.id, "upload_document")

    allow_images = (
        config.IMAGES_ENABLED
        and state.images_left(user_id) > 0
    )

    result = docs.generate(
        state.get_history(user_id),
        instruction,
        docs.get(user_id),
        allow_images=allow_images,
    )

    for _ in range(getattr(result, "images_added", 0)):
        state.count_image(user_id)

    docs.store(user_id, result.name, result.text)

    send_document(
        message,
        result.name,
        result.data,
        result.summary,
    )

    state.count_file(user_id)
    state.remember(
        user_id,
        instruction,
        f"[Fayl yuborildi: {result.name}] {result.summary}",
    )


# ============================================================
# SO'ROVNI TO'G'RI FUNKSIYAGA YO'NALTIRISH
# ============================================================

def route_text(message, text):
    user_id = message.from_user.id
    normalized = _norm(text)

    last_image = (
        state.get_image(user_id)
        if config.IMAGES_ENABLED
        else None
    )
    uploaded_doc = docs.get(user_id)

    wants_image = (
        config.IMAGES_ENABLED
        and looks_like_image_request(text, last_image is not None)
    )
    wants_file = bool(
        _FILE_HINT.search(normalized)
        or (uploaded_doc and _DOCEDIT_HINT.search(normalized))
    )

    action = "chat"
    prompt = ""

    if wants_image or wants_file:
        try:
            action, prompt = llm.classify_vote(
                text,
                last_image is not None,
                uploaded_doc is not None,
            )
        except Exception:
            log.exception("So'rov turini aniqlashda xato")

            if wants_image:
                if last_image is not None and _EDIT_HINT.search(normalized):
                    action = "edit"
                elif _DRAW_HINT.search(normalized):
                    action = "draw"
            elif wants_file:
                action = "file"

    if (
        action == "chat"
        and last_image is not None
        and _IMAGE_FOLLOWUP_HINT.search(normalized)
        and not wants_file
    ):
        do_chat(message, text, image=last_image)
        return

    if action in ("draw", "edit") and not config.IMAGES_ENABLED:
        action = "chat"

    if action == "draw":
        do_draw(message, llm.refine_prompt(prompt or text), text)

    elif action == "edit":
        do_edit(
            message,
            last_image,
            llm.refine_prompt(prompt or text),
            text,
        )

    elif action == "file":
        do_file(message, text)

    else:
        do_chat(message, text)


# ============================================================
# RASM XABARLARI
# ============================================================

def handle_image(message, file_id, caption):
    try:
        file_info = bot.get_file(file_id)
        raw_image = bot.download_file(file_info.file_path)
        image_bytes = images.shrink(raw_image)
    except Exception:
        log.exception("Rasmni yuklab olish yoki o'qishda xato")
        reply(message, "🖼 Rasmni o'qib bo'lmadi. JPG yoki PNG yuborib ko'ring.")
        return

    state.set_image(message.from_user.id, image_bytes)
    caption = (caption or "").strip()

    if caption and config.IMAGES_ENABLED and _EDIT_HINT.search(_norm(caption)):
        do_edit(
            message,
            image_bytes,
            llm.refine_prompt(caption),
            caption,
        )
        return

    question = caption or "Bu rasmda nima bor? Batafsil tushuntirib ber."
    do_chat(
        message,
        question,
        image=image_bytes,
        label=f"[Rasm yuborildi] {question}",
    )


# ============================================================
# BUYRUQLAR
# ============================================================

@bot.message_handler(commands=["start"], chat_types=["private"])
@protected
def cmd_start(message):
    reply(
        message,
        welcome_text(message.from_user),
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


@bot.message_handler(commands=["help"], chat_types=["private"])
@protected
def cmd_help(message):
    reply(
        message,
        HELP,
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


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
    reply(
        message,
        "🎭 <b>Javob rejimini tanlang:</b>",
        parse_mode="HTML",
        reply_markup=modes_menu(state.get_mode(message.from_user.id)),
    )


@bot.message_handler(commands=["rasm"], chat_types=["private"])
@protected
def cmd_draw(message):
    prompt = (message.text or "").partition(" ")[2].strip()

    if not prompt:
        reply(message, "Tavsif yozing:\n/rasm quyosh botayotgan dengiz")
        return

    do_draw(message, llm.refine_prompt(prompt), prompt)


@bot.message_handler(commands=["tahrir"], chat_types=["private"])
@protected
def cmd_edit(message):
    instruction = (message.text or "").partition(" ")[2].strip()

    if not instruction:
        reply(message, "Ko'rsatma yozing:\n/tahrir fonni ko'k rangga o'zgartir")
        return

    image_bytes = state.get_image(message.from_user.id)
    if image_bytes is None:
        reply(message, "✏️ Avval tahrirlash uchun rasm yuboring.")
        return

    do_edit(
        message,
        image_bytes,
        llm.refine_prompt(instruction),
        instruction,
    )


@bot.message_handler(commands=["fayl"], chat_types=["private"])
@protected
def cmd_file(message):
    instruction = (message.text or "").partition(" ")[2].strip()

    if not instruction:
        reply(
            message,
            "Qanday fayl kerakligini yozing:\n\n"
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

    mode = MODES[state.get_mode(user_id)][0]

    text = (
        "🤖 <b>BOT HOLATI</b>\n\n"
        f"🧠 Sozlangan provayderlar: {html.escape(', '.join(providers) or 'Topilmadi')}\n"
        f"🌐 Brave Search: {'Sozlangan' if config.BRAVE_SEARCH_ENABLED else 'Kalit topilmadi'}\n"
        f"🎨 Rasm yaratish: {'Yoqilgan' if config.IMAGES_ENABLED else 'O‘chiq'}\n"
        f"🎭 Rejim: {html.escape(str(mode))}\n"
        f"🖼 Rasm limiti qoldig'i: {state.images_left(user_id)}\n"
        f"📄 Fayl limiti qoldig'i: {state.files_left(user_id)}\n"
        f"💬 Xotiradagi xabarlar: {len(state.get_history(user_id))}\n\n"
        "Eslatma: bu yerda kalit mavjudligi tekshiriladi; "
        "barcha API xizmatlari jonli sinovdan o'tkazilmaydi."
    )

    reply(message, text, parse_mode="HTML")


@bot.message_handler(commands=["qidir"], chat_types=["private"])
@protected
def cmd_search(message):
    query = (message.text or "").partition(" ")[2].strip()

    if not query:
        reply(message, "🔎 So'rov yozing:\n/qidir sun'iy intellekt yangiliklari")
        return

    if not config.BRAVE_SEARCH_ENABLED:
        reply(
            message,
            "🌐 Brave Search API kaliti sozlanmagan. "
            "Northflank'dagi BRAVE_API_KEY qiymatini tekshiring.",
        )
        return

    bot.send_chat_action(message.chat.id, "typing")

    results = llm.web_search(query, max_results=5)
    if not results:
        reply(
            message,
            "🔎 Qidiruv natijasi topilmadi yoki xizmat javob bermadi. "
            "So'rovni o'zgartirib qayta urinib ko'ring.",
        )
        return

    system = MODES[state.get_mode(message.from_user.id)][1]
    system += (
        "\n\nQuyidagi internet qidiruv natijalaridan foydalan. "
        "Manbalarda bo'lmagan faktlarni uydirma. "
        "Agar ma'lumot yetarli bo'lmasa, buni ochiq ayt.\n\n"
        + str(results)
    )

    answer = llm.chat(
        state.get_history(message.from_user.id),
        query,
        system,
    )

    state.remember(
        message.from_user.id,
        f"[Internet qidiruvi] {query}",
        answer,
    )
    send_long(message, answer)


# ============================================================
# TUGMALAR
# ============================================================

def _edit_message(chat_id, message_id, text, markup):
    try:
        bot.edit_message_text(
            text,
            chat_id,
            message_id,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except ApiTelegramException as exc:
        if "not modified" not in str(exc).lower():
            raise


def _edit_markup(chat_id, message_id, markup):
    try:
        bot.edit_message_reply_markup(
            chat_id,
            message_id,
            reply_markup=markup,
        )
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
            chat_id = call.message.chat.id
            message_id = call.message.message_id
            data = call.data or ""

            if data == "menu:modes":
                _edit_message(
                    chat_id,
                    message_id,
                    "🎭 <b>Javob rejimini tanlang:</b>",
                    modes_menu(state.get_mode(user_id)),
                )

            elif data.startswith("mode:"):
                key = data.split(":", 1)[1]
                if key in MODES:
                    state.set_mode(user_id, key)
                    _edit_markup(chat_id, message_id, modes_menu(key))
                    toast = f"Rejim: {MODES[key][0]}"

            elif data == "menu:clear":
                clear_all(user_id)
                toast = "🧹 Suhbat tozalandi"

            elif data == "menu:help":
                _edit_message(chat_id, message_id, HELP, main_menu())

            elif data == "menu:back":
                _edit_message(
                    chat_id,
                    message_id,
                    welcome_text(call.from_user),
                    main_menu(),
                )

    except Exception:
        log.exception("Tugma callback xatosi")
        toast = "Xatolik yuz berdi"

    finally:
        try:
            bot.answer_callback_query(call.id, text=toast)
        except Exception:
            log.debug("Callback javobini yuborib bo'lmadi")


# ============================================================
# MATN, RASM, HUJJAT VA OVOZ HANDLERLARI
# ============================================================

@bot.message_handler(
    content_types=["text"],
    func=lambda m: not (m.text or "").startswith("/"),
    chat_types=["private"],
)
@protected
def on_text(message):
    route_text(message, message.text or "")


@bot.message_handler(content_types=["photo"], chat_types=["private"])
@protected
def on_photo(message):
    handle_image(message, message.photo[-1].file_id, message.caption)


@bot.message_handler(
    content_types=["document"],
    func=lambda m: bool(
        m.document
        and (m.document.mime_type or "").startswith("image/")
    ),
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
        reply(
            message,
            f"📄 Fayl juda katta. Maksimal hajm: "
            f"{docs.MAX_FILE_BYTES // (1024 * 1024)} MB.",
        )
        return

    name = document.file_name or "fayl"
    mime_type = document.mime_type or ""

    docs.precheck(name, mime_type)

    bot.send_chat_action(message.chat.id, "typing")
    file_info = bot.get_file(document.file_id)
    raw = bot.download_file(file_info.file_path)

    text, truncated = docs.extract(name, raw, mime_type)
    user_id = message.from_user.id

    docs.store(user_id, name, text, truncated)

    if truncated:
        reply(
            message,
            f"📄 «{name}» katta fayl. Dastlabki "
            f"taxminan {docs.MAX_TEXT_CHARS} belgisi o'qildi.",
        )

    caption = (message.caption or "").strip()

    if not caption:
        do_chat(
            message,
            f"Men «{name}» faylini yubordim. Mazmunini 3–5 gapda "
            "tushuntir va asosiy fikrlarini sanab ber.",
            label=f"[Fayl yuklandi] {name}",
        )
        return

    try:
        action, _ = llm.classify_vote(caption, False, True)
    except Exception:
        log.exception("Fayl ko'rsatmasini tasniflash xatosi")
        action = "file" if _FILE_HINT.search(caption) else "chat"

    if action == "file":
        do_file(message, caption)
    else:
        do_chat(message, caption, label=f"[Fayl: {name}] {caption}")


@bot.message_handler(content_types=["voice"], chat_types=["private"])
@protected
def on_voice(message):
    voice = message.voice

    if (voice.duration or 0) > config.VOICE_MAX_SECONDS:
        reply(
            message,
            f"🎤 Ovozli xabar juda uzun. Maksimal davomiylik: "
            f"{config.VOICE_MAX_SECONDS} soniya.",
        )
        return

    bot.send_chat_action(message.chat.id, "typing")
    file_info = bot.get_file(voice.file_id)
    audio_bytes = bot.download_file(file_info.file_path)

    text = llm.transcribe(audio_bytes, filename="voice.ogg")
    if not text:
        reply(message, "🎤 Ovozli xabarni tushunib bo'lmadi. Qayta urinib ko'ring.")
        return

    reply(
        message,
        "🎤 <b>Tushunilgan matn:</b>\n" + html.escape(text[:1000]),
        parse_mode="HTML",
    )
    route_text(message, text)


@bot.message_handler(
    content_types=[
        "video", "audio", "sticker", "video_note",
        "animation", "location", "contact",
    ],
    chat_types=["private"],
)
def on_other(message):
    if is_allowed(message.from_user.id):
        reply(
            message,
            "📝 Hozircha matn, rasm, ovozli xabar va hujjatlarni "
            "qayta ishlay olaman.",
        )


# ============================================================
# NORTHFLANK HEALTH-CHECK
# ============================================================

class _Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
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
        server = ThreadingHTTPServer(("0.0.0.0", port), _Health)
    except OSError:
        log.exception("Health-check server ishga tushmadi")
        return

    threading.Thread(
        target=server.serve_forever,
        daemon=True,
    ).start()

    log.info("Health-check server %s-portda ishlayapti", port)


# ============================================================
# ISHGA TUSHIRISH VA TO'XTATISH
# ============================================================

def _shutdown(signum, _frame):
    log.info("Signal %s qabul qilindi. Bot to'xtatilmoqda.", signum)
    try:
        bot.stop_polling()
    except Exception:
        log.exception("Pollingni to'xtatishda xato")


def main():
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    start_health_server()

    # Eski webhook long pollingga xalaqit bermasligi uchun.
    bot.remove_webhook()
    me = bot.get_me()

    commands = [
        types.BotCommand("start", "Botni boshlash"),
        types.BotCommand("help", "Yordam"),
        types.BotCommand("mode", "Javob rejimini tanlash"),
        types.BotCommand("clear", "Suhbatni tozalash"),
        types.BotCommand("status", "Bot holati va limitlar"),
        types.BotCommand("qidir", "Internetdan qidirish"),
        types.BotCommand("rasm", "Rasm yaratish"),
        types.BotCommand("tahrir", "Oxirgi rasmni tahrirlash"),
        types.BotCommand("fayl", "Fayl yoki taqdimot yaratish"),
        types.BotCommand("id", "Telegram ID"),
    ]

    try:
        bot.set_my_commands(commands)
    except Exception:
        log.exception("Telegram buyruqlarini o'rnatib bo'lmadi")

    log.info(
        "Bot ishga tushdi | username=@%s | model=%s | "
        "Brave=%s | rasm=%s",
        me.username,
        config.CHAT_MODEL,
        "sozlangan" if config.BRAVE_SEARCH_ENABLED else "sozlanmagan",
        "yoqilgan" if config.IMAGES_ENABLED else "o'chiq",
    )

    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
