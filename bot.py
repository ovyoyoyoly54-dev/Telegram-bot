python
"""
AI Telegram Bot
Imkoniyatlar:
- AI bilan suhbat va suhbat tarixidan foydalanish
- Groq, OpenRouter va Gemini orqali AI javoblari
- Internet qidiruvi
- Rasmni tushunish, yaratish va tahrirlash
- PDF, DOCX, XLSX, PPTX, TXT, CSV va boshqa hujjatlar
- Ovozli xabarni matnga aylantirish
- PowerPoint taqdimot yaratish
- Foydalanuvchi limitlari va ruxsat nazorati
- Northflank health-check va long polling
"""

import functools
import html
import io
import logging
import os
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
# LOGGING VA TELEGRAM BOT
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
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
# FOYDALANUVCHI MATNLARI
# ============================================================

WELCOME = (
    "👋 <b>Salom, {name}!</b>\n\n"
    "Men AI yordamchiman 🤖\n\n"
    "💬 Savol bering — javob beraman.\n"
    "🎤 Ovozli xabar yuboring — matnga aylantiraman.\n"
    "🖼 Rasm yuboring — mazmunini tushuntiraman.\n"
    "🎨 Rasm chizishni so'rang — yaratishga harakat qilaman.\n"
    "✏️ Rasm yuborib, qanday o'zgartirish kerakligini yozing.\n"
    "📄 Hujjat yuboring — o'qish, xulosa va tahrirlashga yordam beraman.\n"
    "📊 Excel jadvali yoki PowerPoint taqdimot so'rashingiz mumkin.\n\n"
    "Batafsil imkoniyatlar uchun /help buyrug'ini yuboring."
)

HELP = (
    "ℹ️ <b>BOT YORDAMI</b>\n\n"
    "💬 <b>Suhbat</b>\n"
    "Oddiy savol yozing. Bot oldingi suhbat kontekstidan foydalanadi.\n\n"
    "🧠 <b>Buyruqlar</b>\n"
    "/start — botni boshlash\n"
    "/help — yordam\n"
    "/mode — javob rejimini tanlash\n"
    "/clear — suhbat va vaqtinchalik fayl kontekstini tozalash\n"
    "/id — Telegram ID\n"
    "/status — bot holati va limitlar\n"
    "/qidir so'rov — internetdan qidirish\n\n"
    "🎨 <b>Rasmlar</b>\n"
    "/rasm tavsif — rasm yaratish\n"
    "/tahrir o'zgarish — oxirgi rasmni tahrirlash\n"
    "Rasm yuborib, tagiga savol yozishingiz ham mumkin.\n\n"
    "📁 <b>Fayllar</b>\n"
    "/fayl tavsif — hujjat yoki taqdimot yaratish\n"
    "PDF, DOCX, XLSX, PPTX, TXT, CSV, JSON va qo'llab-quvvatlanadigan "
    "boshqa formatdagi fayllarni yuboring.\n"
    "Fayl ostiga savol yoki o'zgartirish ko'rsatmasini yozing.\n\n"
    "📊 Misollar:\n"
    "/fayl Sun'iy intellekt haqida 8 slaydli taqdimot\n"
    "/fayl 10 ta mahsulot uchun Excel narxlar jadvali\n"
    "/qidir Bugungi texnologiya yangiliklari\n\n"
    "Eslatma: AI javoblari va qidiruv natijalarida xatolar bo'lishi mumkin."
)


# ============================================================
# SO'ROVLARNI ANIQLASH
# ============================================================

_DRAW_HINT = re.compile(
    r"(\brasm|\bsurat|\bchiz|\btasvir|illyustr|"
    r"\bdraw|\bpaint|\bsketch|\bimage|\bpicture|"
    r"\blogo|\bposter|generate image|create image|"
    r"создай изображение|расм|сурат|чиз|тасвир)",
    re.I,
)

_EDIT_HINT = re.compile(
    r"(\bfon|\brang|o'zgartir|zgartir|\bqora|oq-qora|"
    r"\bqo'?sh|tashla|almashtir|\bedit|\bbackground|"
    r"\bremove|\bfilter|\buslub|\bизмен|фон|ранг)",
    re.I,
)

_FILE_HINT = re.compile(
    r"(\bfayl|\bfile\b|\bdocx|\bword\b|\bpdf|\bexcel|"
    r"\bxlsx|\bcsv|\btxt\b|\bjson|\bhujjat|\bjadval|"
    r"\brezyume|\bresume|\bhisobot|\bprezentatsiya|"
    r"\bpresentation|\btaqdimot|\bslayd|\bslide|"
    r"\bpptx|\bpowerpoint|\bфайл|\bпрезентация)",
    re.I,
)

_REALTIME_HINT = re.compile(
    r"(bugun|bugungi|hozir|hozirgi|kecha|ertaga|joriy yil|"
    r"shu yil|real.?vaqt|yangi|oxirgi|eng so'nggi|kurs|"
    r"ob.?havo|narx|natija|hisob|o'?yin|match|futbol|"
    r"football|soccer|premier league|la liga|champions league|"
    r"\bucl\b|chempionlar|liga|turnir|ochko|gollar|transfer|"
    r"tarkib|fikstura|fixtures|schedule|standings|kim o'ynaydi|"
    r"kim oynaydi|kim bilan|qachon|soat necha|yangilik|voqea|"
    r"valyuta|sana|qaysi kun|nechanchi|сегодня|сейчас|курс|"
    r"погода|новости|футбол|матч|расписание)",
    re.I,
)

_DOCEDIT_HINT = re.compile(
    r"(tahrir|tuzat|qayta yoz|o'zgartir|zgartir|tarjima|"
    r"qo'?sh|o'chir|qisqartir|kengaytir|formatla|"
    r"to'g'rila|to'ldir|\bedit\b|\btranslate\b|\bfix\b|"
    r"\brewrite\b|\bconvert\b|o'tkaz)",
    re.I,
)

_IMAGE_FOLLOWUP_HINT = re.compile(
    r"(shu rasm|bu rasm|o'sha rasm|oldingi rasm|"
    r"rasmdagi|suratdagi|shu surat|bu surat|"
    r"unda nima|uning rangi|rasmda nima|"
    r"this image|that image|this picture|describe it)",
    re.I,
)


def _norm(text: str) -> str:
    """Matnni normal holatga keltirish."""
    if not text:
        return ""

    return (
        text.replace("ʻ", "'")
        .replace("‘", "'")
        .replace("’", "'")
        .replace("`", "'")
    )


def looks_like_image_request(text: str, has_image: bool) -> bool:
    text = _norm(text)
    return bool(
        _DRAW_HINT.search(text)
        or (has_image and _EDIT_HINT.search(text))
    )


# ============================================================
# TELEGRAM XABAR YUBORISH YORDAMCHILARI
# ============================================================

def _send(chat_id: int, text: str, reply_to: int | None = None, **kwargs):
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


def reply(message, text: str, **kwargs):
    return _send(
        message.chat.id,
        text,
        message.message_id,
        **kwargs,
    )


def send_long(message, text: str) -> None:
    """Uzun javobni qismlarga bo'lib yuboradi."""
    first = True

    for chunk in split_text(text or "", config.TELEGRAM_MAX_CHARS):
        if not chunk.strip():
            continue

        reply_to = message.message_id if first else None
        first = False

        try:
            _send(
                message.chat.id,
                to_telegram_html(chunk),
                reply_to,
                parse_mode="HTML",
            )
        except ApiTelegramException:
            # HTML formatlash xatosida oddiy matnga o'tamiz.
            _send(message.chat.id, chunk, reply_to)


def send_photo(message, image_bytes: bytes) -> None:
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


def send_document(
    message,
    name: str,
    data: bytes,
    caption: str = "",
) -> None:
    def _send_file(reply_to):
        file_obj = io.BytesIO(data)
        file_obj.name = name

        bot.send_document(
            message.chat.id,
            file_obj,
            caption=(caption[:1000] or None),
            reply_to_message_id=reply_to,
        )

    try:
        _send_file(message.message_id)
    except ApiTelegramException as exc:
        if "reply" in str(exc).lower():
            _send_file(None)
        else:
            raise


def _safe_reply(message, text: str) -> None:
    try:
        reply(message, text)
    except Exception:
        log.exception("Xato xabarini yuborib bo'lmadi")


def clear_all(user_id: int) -> None:
    """Suhbat tarixini va vaqtinchalik hujjat kontekstini tozalaydi."""
    state.clear_history(user_id)
    docs.clear(user_id)


def welcome_text(user) -> str:
    return WELCOME.format(
        name=html.escape(user.first_name or "do'st")
    )


# ============================================================
# RUXSAT, LIMIT VA UMUMIY XATOLAR
# ============================================================

def is_allowed(user_id: int) -> bool:
    return (
        not config.ALLOWED_USERS
        or user_id in config.ALLOWED_USERS
    )


def protected(func):
    """Ruxsat va foydalanuvchi navbatini tekshiradi."""

    @functools.wraps(func)
    def wrapper(message, *args, **kwargs):
        if not message.from_user:
            return

        user_id = message.from_user.id

        if not is_allowed(user_id):
            _safe_reply(
                message,
                f"⛔ Bu botdan foydalanishga ruxsat yo'q.\n"
                f"Telegram ID: {user_id}",
            )
            return

        wait = state.rate_wait(user_id)

        if wait > 0:
            _safe_reply(
                message,
                f"⏳ Biroz kuting: taxminan {wait:.0f} soniyadan keyin "
                "qayta urinib ko'ring.",
            )
            return

        with state.user_slot(user_id) as allowed:
            if not allowed:
                _safe_reply(
                    message,
                    "⏳ Oldingi so'rovingiz hali bajarilmoqda. "
                    "Iltimos, biroz kuting.",
                )
                return

            try:
                return func(message, *args, **kwargs)

            except (
                llm.LLMError,
                images.ImageError,
                docs.DocError,
            ) as exc:
                log.warning(
                    "Foydalanuvchi so'rovi bajarilmadi: %s",
                    exc,
                    exc_info=True,
                )
                _safe_reply(
                    message,
                    "⚠️ So'rovni bajarib bo'lmadi.\n"
                    f"Sabab: {str(exc)[:700]}",
                )

            except ApiTelegramException:
                log.exception("Telegram API xatosi")
                _safe_reply(
                    message,
                    "⚠️ Telegram xabarni yuborishda muammo yuz berdi. "
                    "Birozdan keyin qayta urinib ko'ring.",
                )

            except Exception:
                log.exception("Kutilmagan xatolik")
                _safe_reply(
                    message,
                    "❌ Kutilmagan xatolik yuz berdi. "
                    "Keyinroq qayta urinib ko'ring.",
                )

    return wrapper


# ============================================================
# AI BILAN SUHBAT
# ============================================================

def do_chat(
    message,
    text: str,
    image: bytes | None = None,
    label: str | None = None,
) -> None:
    """AI javobini oladi va suhbat tarixiga saqlaydi."""
    user_id = message.from_user.id

    bot.send_chat_action(message.chat.id, "typing")

    system = MODES[state.get_mode(user_id)][1]

    uploaded_doc = docs.get(user_id)
    if uploaded_doc:
        system += docs.context_block(uploaded_doc)

    history = state.get_history(user_id)

    if not image and _REALTIME_HINT.search(_norm(text)):
        log.info("Internet qidiruvi kerak bo'lishi mumkin: %s", text)

        answer = llm.chat_with_search(
            history,
            text,
            system,
        )
    else:
        try:
            answer = llm.chat_multi(
                history,
                text,
                system,
                image=image,
            )
        except Exception as exc:
            log.warning(
                "Ko'p provayderli chat xatosi, zaxira usuli: %s",
                exc,
                exc_info=True,
            )

            answer = llm.chat(
                history,
                text,
                system,
                image=image,
            )

    state.remember(
        user_id,
        label or text,
        answer,
    )

    send_long(message, answer)


# ============================================================
# RASM YARATISH VA TAHRIRLASH
# ============================================================

def _deliver_image(
    message,
    image_bytes: bytes,
    note: str,
) -> None:
    user_id = message.from_user.id

    if not image_bytes:
        raise images.ImageError("Rasm yaratish xizmati bo'sh natija qaytardi.")

    # Rasmni saqlash va keyingi savollarda ishlatish.
    try:
        state.set_image(user_id, images.shrink(image_bytes))
    except Exception:
        log.warning(
            "Rasmni suhbat xotirasiga saqlash amalga oshmadi",
            exc_info=True,
        )

    send_photo(message, image_bytes)
    state.count_image(user_id)
    state.remember(user_id, note, "[Rasm yuborildi]")


def _check_image_quota(message) -> bool:
    if not config.IMAGES_ENABLED:
        reply(
            message,
            "🎨 Rasm funksiyasi hozircha sozlanmagan.",
        )
        return False

    if state.images_left(message.from_user.id) <= 0:
        reply(
            message,
            f"🎨 Bugungi rasm limiti "
            f"({config.IMAGE_DAILY_LIMIT} ta) tugadi. "
            "Ertaga qayta urinib ko'ring.",
        )
        return False

    return True


def do_draw(message, prompt: str, note: str) -> None:
    if not _check_image_quota(message):
        return

    bot.send_chat_action(message.chat.id, "upload_photo")

    result = images.make_image(prompt)
    _deliver_image(message, result, note)


def do_edit(
    message,
    image_bytes: bytes | None,
    prompt: str,
    note: str,
) -> None:
    if image_bytes is None:
        reply(
            message,
            "✏️ Avval tahrirlash uchun rasm yuboring.",
        )
        return

    if not _check_image_quota(message):
        return

    bot.send_chat_action(message.chat.id, "upload_photo")

    result = images.edit_image(image_bytes, prompt)
    _deliver_image(message, result, note)


# ============================================================
# FAYL VA TAQDIMOT YARATISH
# ============================================================

def do_file(message, instruction: str) -> None:
    user_id = message.from_user.id

    if (
        config.FILE_DAILY_LIMIT > 0
        and state.files_left(user_id) <= 0
    ):
        reply(
            message,
            f"📄 Bugungi fayl limiti "
            f"({config.FILE_DAILY_LIMIT} ta) tugadi.",
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

    # Taqdimotdagi har bir muvaffaqiyatli rasmni hisoblash.
    if result.images_added:
        for _ in range(result.images_added):
            state.count_image(user_id)

    docs.store(
        user_id,
        result.name,
        result.text,
    )

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
# MATNNI TO'G'RI YO'NALISHGA UZATISH
# ============================================================

def route_text(message, text: str) -> None:
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
        or (
            uploaded_doc
            and _DOCEDIT_HINT.search(normalized)
        )
    )

    action = "chat"
    prompt = ""

    # Tasniflashni faqat maxsus funksiya talab qilinganda ishlatamiz.
    if wants_image or wants_file:
        try:
            action, prompt = llm.classify_vote(
                text,
                last_image is not None,
                uploaded_doc is not None,
            )
        except Exception:
            log.exception("So'rovni tasniflash muvaffaqiyatsiz bo'ldi")

            if wants_image:
                if last_image is not None and _EDIT_HINT.search(normalized):
                    action = "edit"
                elif _DRAW_HINT.search(normalized):
                    action = "draw"
            elif wants_file:
                action = "file"

    # Oldingi rasm haqida davomiy savol.
    if (
        action == "chat"
        and last_image is not None
        and _IMAGE_FOLLOWUP_HINT.search(normalized)
        and not wants_file
    ):
        do_chat(
            message,
            text,
            image=last_image,
        )
        return

    if action in ("draw", "edit") and not config.IMAGES_ENABLED:
        action = "chat"

    if action == "draw":
        prompt = llm.refine_prompt(text)
        do_draw(message, prompt or text, text)

    elif action == "edit":
        prompt = llm.refine_prompt(text)
        do_edit(
            message,
            state.get_image(user_id),
            prompt or text,
            text,
        )

    elif action == "file":
        do_file(message, text)

    else:
        do_chat(message, text)


# ============================================================
# RASM XABARLARI
# ============================================================

def handle_image(message, file_id: str, caption: str | None) -> None:
    try:
        file_info = bot.get_file(file_id)
        raw_image = bot.download_file(file_info.file_path)
        image_bytes = images.shrink(raw_image)
    except Exception:
        log.exception("Rasmni yuklab olish yoki o'qish muvaffaqiyatsiz")
        reply(
            message,
            "🖼 Rasmni o'qib bo'lmadi. JPG yoki PNG formatida "
            "qayta yuborib ko'ring.",
        )
        return

    state.set_image(message.from_user.id, image_bytes)

    caption = (caption or "").strip()

    if caption and config.IMAGES_ENABLED:
        if caption.lower().startswith("/tahrir"):
            instruction = caption.partition(" ")[2].strip()

            if not instruction:
                reply(
                    message,
                    "Nima o'zgartirish kerakligini yozing:\n"
                    "/tahrir fonni tun qil",
                )
                return

            do_edit(
                message,
                image_bytes,
                llm.refine_prompt(instruction),
                caption,
            )
            return

        if _EDIT_HINT.search(_norm(caption)):
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

@bot.message_handler(commands=["id"], chat_types=["private"])
@protected
def cmd_id(message):
    reply(
        message,
        f"🆔 Telegram ID: {message.from_user.id}",
    )


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


@bot.message_handler(commands=["clear"], chat_types=["private"])
@protected
def cmd_clear(message):
    clear_all(message.from_user.id)

    reply(
        message,
        "🧹 Suhbat tarixi va vaqtinchalik hujjat konteksti tozalandi.",
    )


@bot.message_handler(commands=["mode"], chat_types=["private"])
@protected
def cmd_mode(message):
    reply(
        message,
        "🎭 <b>Javob rejimini tanlang:</b>",
        parse_mode="HTML",
        reply_markup=modes_menu(
            state.get_mode(message.from_user.id)
        ),
    )


@bot.message_handler(commands=["rasm"], chat_types=["private"])
@protected
def cmd_draw(message):
    prompt = (message.text or "").partition(" ")[2].strip()

    if not prompt:
        reply(
            message,
            "Tavsif yozing:\n"
            "/rasm quyosh botayotgan dengiz",
        )
        return

    if not _check_image_quota(message):
        return

    do_draw(
        message,
        llm.refine_prompt(prompt),
        prompt,
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
            "/fayl 5 ta mahsulot uchun Excel jadvali\n"
            "/fayl O'zbekistonda ekologiya haqida Word hujjati",
        )
        return

    do_file(message, instruction)


@bot.message_handler(commands=["tahrir"], chat_types=["private"])
@protected
def cmd_edit(message):
    instruction = (message.text or "").partition(" ")[2].strip()

    if not instruction:
        reply(
            message,
            "Nima o'zgartirish kerakligini yozing:\n"
            "/tahrir fonni tun rangiga o'zgartir",
        )
        return

    image_bytes = state.get_image(message.from_user.id)

    if image_bytes is None:
        reply(
            message,
            "✏️ Avval tahrirlash uchun rasm yuboring.",
        )
        return

    if not _check_image_quota(message):
        return

    do_edit(
        message,
        image_bytes,
        llm.refine_prompt(instruction),
        instruction,
    )


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

    status_text = (
        "🤖 <b>BOT HOLATI</b>\n\n"
        f"🧠 Sozlangan AI provayderlari: "
        f"{html.escape(', '.join(providers) or 'Topilmadi')}\n"
        f"🎨 Rasm yaratish: "
        f"{'Yoqilgan' if config.IMAGES_ENABLED else 'O‘chiq'}\n"
        f"🎭 Javob rejimi: {html.escape(str(mode))}\n"
        f"🖼 Bugungi rasm limiti qoldig'i: "
        f"{state.images_left(user_id)}\n"
        f"📄 Bugungi fayl limiti qoldig'i: "
        f"{state.files_left(user_id)}\n"
        f"💬 Xotiradagi yozuvlar: "
        f"{len(state.get_history(user_id))}\n\n"
        "Eslatma: bu tekshiruv API xizmatlarining haqiqiy ishlashini "
        "sinamaydi, faqat sozlangan kalitlar mavjudligini ko'rsatadi."
    )

    reply(
        message,
        status_text,
        parse_mode="HTML",
    )


@bot.message_handler(commands=["qidir"], chat_types=["private"])
@protected
def cmd_search(message):
    query = (message.text or "").partition(" ")[2].strip()

    if not query:
        reply(
            message,
            "🔎 Qidiruv so'rovini yozing:\n"
            "/qidir bugungi texnologiya yangiliklari",
        )
        return

    bot.send_chat_action(message.chat.id, "typing")

    results = llm.web_search(query, max_results=5)

    if not results:
        reply(
            message,
            "🔎 Qidiruv natijasi topilmadi. "
            "Boshqa so'zlar bilan urinib ko'ring.",
        )
        return

    system = MODES[state.get_mode(message.from_user.id)][1]
    system += (
        "\n\nQuyidagi internet qidiruv natijalaridan foydalan. "
        "Manbalarda bo'lmagan faktlarni o'ylab topma. "
        "Agar manbalar qarama-qarshi bo'lsa yoki ma'lumot yetarli "
        "bo'lmasa, buni ochiq ayt.\n\n"
        + results
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
# TUGMALAR VA CALLBACKLAR
# ============================================================

def _edit_message(chat_id: int, message_id: int, text: str, markup):
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


def _edit_markup(chat_id: int, message_id: int, markup):
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
    user_id = call.from_user.id
    toast = None

    try:
        if not is_allowed(user_id):
            toast = "⛔ Ruxsat yo'q"

        elif call.message is not None:
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
                    _edit_markup(
                        chat_id,
                        message_id,
                        modes_menu(key),
                    )
                    toast = f"Rejim: {MODES[key][0]}"

            elif data == "menu:clear":
                clear_all(user_id)
                toast = "🧹 Suhbat tozalandi"

            elif data == "menu:help":
                _edit_message(
                    chat_id,
                    message_id,
                    HELP,
                    main_menu(),
                )

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
            bot.answer_callback_query(
                call.id,
                text=toast,
            )
        except Exception:
            log.debug("Callback javobini yuborib bo'lmadi")


# ============================================================
# MATNLI XABARLAR
# ============================================================

@bot.message_handler(
    content_types=["text"],
    func=lambda message: not (message.text or "").startswith("/"),
    chat_types=["private"],
)
@protected
def on_text(message):
    route_text(message, message.text or "")


# ============================================================
# RASM XABARLARI
# ============================================================

@bot.message_handler(
    content_types=["photo"],
    chat_types=["private"],
)
@protected
def on_photo(message):
    handle_image(
        message,
        message.photo[-1].file_id,
        message.caption,
    )


@bot.message_handler(
    content_types=["document"],
    func=lambda message: bool(
        message.document
        and (message.document.mime_type or "").startswith("image/")
    ),
    chat_types=["private"],
)
@protected
def on_image_document(message):
    if (message.document.file_size or 0) > 10 * 1024 * 1024:
        reply(
            message,
            "🖼 Rasm juda katta. 10 MB dan kichik rasm yuboring.",
        )
        return

    handle_image(
        message,
        message.document.file_id,
        message.caption,
    )


# ============================================================
# HUJJAT YUKLASH VA O'QISH
# ============================================================

@bot.message_handler(
    content_types=["document"],
    chat_types=["private"],
)
@protected
def on_file_document(message):
    document = message.document

    if (document.file_size or 0) > docs.MAX_FILE_BYTES:
        reply(
            message,
            "📄 Fayl juda katta. Maksimal hajm: "
            f"{docs.MAX_FILE_BYTES // (1024 * 1024)} MB.",
        )
        return

    name = document.file_name or "fayl"
    mime_type = document.mime_type or ""

    docs.precheck(name, mime_type)

    bot.send_chat_action(message.chat.id, "typing")

    file_info = bot.get_file(document.file_id)
    raw = bot.download_file(file_info.file_path)

    text, truncated = docs.extract(
        name,
        raw,
        mime_type,
    )

    user_id = message.from_user.id

    docs.store(
        user_id,
        name,
        text,
        truncated,
    )

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
            f"Men «{name}» faylini yubordim. "
            "Mazmunini 3–5 gapda tushuntir va asosiy fikrlarini "
            "sanab ber.",
            label=f"[Fayl yuklandi] {name}",
        )
        return

    try:
        action, _ = llm.classify_vote(
            caption,
            False,
            True,
        )
    except Exception:
        log.exception("Fayl ko'rsatmasini tasniflash xatosi")
        action = "file" if _FILE_HINT.search(caption) else "chat"

    if action == "file":
        do_file(message, caption)
    else:
        do_chat(
            message,
            caption,
            label=f"[Fayl: {name}] {caption}",
        )


# ============================================================
# OVOZLI XABARLAR
# ============================================================

@bot.message_handler(
    content_types=["voice"],
    chat_types=["private"],
)
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

    text = llm.transcribe(
        audio_bytes,
        filename="voice.ogg",
    )

    if not text:
        reply(
            message,
            "🎤 Ovozli xabarni tushunib bo'lmadi. "
            "Qayta yozib ko'ring.",
        )
        return

    reply(
        message,
        "🎤 <b>Tushunilgan matn:</b>\n"
        + html.escape(text[:1000]),
        parse_mode="HTML",
    )

    route_text(message, text)


# ============================================================
# QO'LLAB-QUVVATLANMAYDIGAN XABAR TURLARI
# ============================================================

@bot.message_handler(
    content_types=[
        "video",
        "audio",
        "sticker",
        "video_note",
        "animation",
        "location",
        "contact",
    ],
    chat_types=["private"],
)
def on_other(message):
    if is_allowed(message.from_user.id):
        reply(
            message,
            "📝 Hozircha matn, rasm, ovozli xabar va hujjatlarni "
            "(PDF, Word, Excel va boshqalar) qayta ishlay olaman.",
        )


# ============================================================
# NORTHFLANK HEALTH CHECK
# ============================================================

class _Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def start_health_server() -> None:
    """Northflank health-check uchun HTTP server."""
    if not config.HEALTH_PORT:
        return

    try:
        server = ThreadingHTTPServer(
            ("0.0.0.0", config.HEALTH_PORT),
            _Health,
        )
    except OSError:
        log.exception("Health server ishga tushmadi")
        return

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
    )
    thread.start()

    log.info("Health server %s-portda ishlayapti", config.HEALTH_PORT)


# ============================================================
# TO'XTATISH VA ISHGA TUSHIRISH
# ============================================================

def _shutdown(signum, _frame) -> None:
    log.info("Signal %s qabul qilindi. Bot to'xtatilmoqda.", signum)

    try:
        bot.stop_polling()
    except Exception:
        log.exception("Pollingni to'xtatishda xato")


def main() -> None:
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
        log.exception("Telegram buyruqlari ro'yxatini o'rnatib bo'lmadi")

    log.info(
        "@%s ishga tushdi | chat modeli: %s | rasm: %s | "
        "Gemini kaliti: %s | ruxsat: %s",
        me.username,
        config.CHAT_MODEL,
        "yoqilgan" if config.IMAGES_ENABLED else "o'chiq",
        "bor" if config.GEMINI_API_KEY else "yo'q",
        (
            f"{len(config.ALLOWED_USERS)} ta foydalanuvchi"
            if config.ALLOWED_USERS
            else "barcha foydalanuvchilar"
        ),
    )

    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
