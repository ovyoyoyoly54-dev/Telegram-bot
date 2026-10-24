
"""AI Telegram bot: chat, rasm tushunish, rasm chizish/tahrirlash, ovozli xabarlar,
fayl va taqdimot yaratish.

Groq (asosiy) + OpenRouter (zaxira) + Gemini (zaxira) + Cloudflare (rasm).
Northflank'da long-polling bilan ishlaydi.
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

WELCOME = (
    "👋 <b>Salom, {name}!</b>\n\n"
    "Men aqlli AI yordamchiman 🤖\n"
    "• Savol yozing — javob beraman\n"
    "• 🎤 Ovozli xabar yuboring — tushunaman\n"
    "• 🖼 Rasm yuboring — nima borligini aytaman\n"
    "• 🎨 «dengiz ustida quyosh botayotgan rasm chiz» deb yozing — chizaman\n"
    "• ✏️ Rasm yuborib, tagiga «fonni tun qil» deb yozing — tahrirlayman\n"
    "• 📄 Fayl yuboring (PDF, Word, Excel, PowerPoint, TXT...) — o'qiyman, tahrirlab qaytaraman\n"
    "• 📝 «5 ta mijoz uchun narxlar jadvalini excelda tayyorla» yoki "
    "«sun'iy intellekt haqida 8 slaydli taqdimot qil» — tayyor fayl yuboraman"
)

HELP = (
    "ℹ️ <b>Yordam</b>\n\n"
    "/start — botni boshlash\n"
    "/mode — javob rejimini tanlash\n"
    "/clear — suhbatni tozalash\n"
    "/rasm <i>tavsif</i> — rasm chizish\n"
    "/tahrir <i>o'zgarish</i> — oxirgi rasmni tahrirlash\n"
    "/fayl <i>tavsif</i> — fayl yaratish: Word, Excel, PDF, PowerPoint taqdimot, TXT, CSV...\n"
    "/id — Telegram ID'ingiz\n"
    "/help — shu yordam oynasi\n\n"
    "Oddiy matn yozsangiz, AI javob beradi va oldingi xabarlaringizni eslab qoladi.\n\n"
    "📄 <b>Fayllar:</b> PDF, DOCX, XLSX, PPTX, TXT, CSV, JSON va kod fayllarini yuboring. "
    "Tagiga savol yoki «tahrirla», «o'zbekchaga tarjima qil», «jadvalga o'tkaz» kabi ko'rsatma yozing. "
    "Fayl 30 daqiqa eslab turiladi; /clear bilan o'chadi. Katta fayllarning faqat boshi o'qiladi."
)

# Rasm yaratish/tahrirlash so'rovlarini aniqlash.
_DRAW_HINT = re.compile(
    r"(\brasm|\bsurat|\bchiz|\btasvir|illyustr|\bdraw|\bpaint|\bsketch|\bimage|\bpicture"
    r"|\blogo|\bposter|\bgenerate image|\bcreate image|\bсоздай изображение"
    r"|\bрасм|\bсурат|\bчиз|\bтасвир)",
    re.I,
)

_EDIT_HINT = re.compile(
    r"(\bfon|\brang|o'zgartir|zgartir|\bqora|oq-qora|\bqo'?sh|tashla|almashtir"
    r"|\bedit|\bbackground|\bremove|\bfilter|\buslub|\bфон|\bранг|\bизмен)",
    re.I,
)

# Fayl yaratish/tahrirlash ni aniqlash.
_FILE_HINT = re.compile(
    r"(\bfayl|\bfile\b|\bdocx|\bword\b|\bpdf|\bexcel|\bxlsx|\bcsv|\btxt\b|\bjson"
    r"|\bhujjat|\bjadval|\brezyume|\bresume|\bhisobot|\bprezentatsiya|\bpresentation"
    r"|\btaqdimot|\bslayd|\bslide|\bpptx|\bpowerpoint|\bфайл|\bпрезентация)",
    re.I,
)

# Bugungi sana, sport va o'zgarib turadigan ma'lumotlar uchun qidiruv.
_REALTIME_HINT = re.compile(
    r"(bugun|bugungi|hozir|hozirgi|kecha|ertaga|joriy yil|shu yil|real.?vaqt"
    r"|yangi|oxirgi|eng so'nggi|kurs|ob.?havo|narx|natija|hisob|o'?yin|match"
    r"|futbol|football|soccer|premier league|la liga|champions league|ucl"
    r"|chempionlar|liga|turnir|jadval|ochko|gollar|transfer|tarkib|fikstura"
    r"|fixtures|schedule|standings|kim o'ynaydi|kim oynaydi|kim bilan|qachon"
    r"|soat necha|yangilik|voqea|prezident|valyuta|sana|qaysi kun|nechanchi"
    r"|сегодня|сейчас|курс|погода|новости|футбол|матч|расписание)",
    re.I,
)

_DOCEDIT_HINT = re.compile(
    r"(tahrir|tuzat|qayta yoz|o'zgartir|zgartir|tarjima|qo'?sh|o'chir|qisqartir"
    r"|kengaytir|formatla|to'g'rila|to'ldir|\bedit|\btranslate|\bfix|\brewrite"
    r"|\bconvert|o'tkaz)",
    re.I,
)


def _norm(text: str) -> str:
    """Matnni normallashtirish: apostroflarni bir xil ko'rinishga keltirish."""
    if not text:
        return ""
    return (
        text.replace("ʻ", "'")
        .replace("‘", "'")
        .replace("’", "'")
        .replace("`", "'")
    )


def looks_like_image_request(text: str, has_image: bool) -> bool:
    t = _norm(text)
    return bool(_DRAW_HINT.search(t) or (has_image and _EDIT_HINT.search(t)))


# ---------------------------------------------------------------- yuborish yordamchilari

def _send(chat_id: int, text: str, reply_to: int | None = None, **kw):
    try:
        return bot.send_message(
            chat_id, text, reply_to_message_id=reply_to, **kw
        )
    except ApiTelegramException as exc:
        if reply_to and "repl" in str(exc).lower():
            return bot.send_message(chat_id, text, **kw)
        raise


def reply(m, text: str, **kw):
    return _send(m.chat.id, text, m.message_id, **kw)


def send_long(m, text: str) -> None:
    """Uzun javobni bo'laklab yuboradi; HTML xato bo'lsa oddiy matn yuboradi."""
    first = True
    for chunk in split_text(text, config.TELEGRAM_MAX_CHARS):
        if not chunk.strip():
            continue
        reply_to = m.message_id if first else None
        first = False
        try:
            _send(
                m.chat.id,
                to_telegram_html(chunk),
                reply_to,
                parse_mode="HTML",
            )
        except ApiTelegramException:
            _send(m.chat.id, chunk, reply_to)


def send_photo(m, img: bytes) -> None:
    try:
        bot.send_photo(m.chat.id, img, reply_to_message_id=m.message_id)
    except ApiTelegramException as exc:
        if "repl" in str(exc).lower():
            bot.send_photo(m.chat.id, img)
        else:
            raise


def send_document(m, name: str, data: bytes, caption: str = "") -> None:
    def _go(reply_to):
        f = io.BytesIO(data)
        f.name = name
        bot.send_document(
            m.chat.id,
            f,
            caption=(caption[:1000] or None),
            reply_to_message_id=reply_to,
        )

    try:
        _go(m.message_id)
    except ApiTelegramException as exc:
        if "repl" in str(exc).lower():
            _go(None)
        else:
            raise


def clear_all(uid: int) -> None:
    state.clear_history(uid)
    docs.clear(uid)


def welcome_text(user) -> str:
    return WELCOME.format(name=html.escape(user.first_name or "do'st"))


# ---------------------------------------------------------------- himoya (ruxsat, limit, xatolar)

def is_allowed(uid: int) -> bool:
    return not config.ALLOWED_USERS or uid in config.ALLOWED_USERS


def protected(fn):
    """Ruxsat, tezlik limiti, foydalanuvchi navbati va xatolarni boshqarish."""

    @functools.wraps(fn)
    def wrapper(m, *args, **kwargs):
        uid = m.from_user.id

        if not is_allowed(uid):
            reply(m, f"⛔ Kechirasiz, bu bot yopiq. ID'ingiz: {uid}")
            return

        wait = state.rate_wait(uid)
        if wait > 0:
            reply(
                m,
                f"⏳ Iltimos, biroz sekinroq yozing "
                f"({wait:.0f} soniyadan keyin urinib ko'ring).",
            )
            return

        with state.user_slot(uid) as ok:
            if not ok:
                reply(m, "⏳ Oldingi so'rovingiz hali bajarilmoqda, biroz kuting.")
                return

            try:
                fn(m, *args, **kwargs)
            except (llm.LLMError, images.ImageError, docs.DocError) as exc:
                log.warning("Foydalanuvchiga xato: %s", exc, exc_info=True)
                _safe_reply(m, f"⚠️ {exc}")
            except Exception:
                log.exception("Kutilmagan xato")
                _safe_reply(
                    m,
                    "❌ Kutilmagan xatolik yuz berdi. Keyinroq urinib ko'ring.",
                )

    return wrapper


def _safe_reply(m, text: str) -> None:
    try:
        reply(m, text)
    except Exception:
        log.exception("Xato xabarini yuborib bo'lmadi")


# ---------------------------------------------------------------- asosiy chat

def do_chat(
    m,
    text: str,
    image: bytes | None = None,
    label: str | None = None,
) -> None:
    """Oddiy suhbat; real-vaqt savollarida internet qidiruvidan foydalanadi."""
    uid = m.from_user.id
    bot.send_chat_action(m.chat.id, "typing")

    system = MODES[state.get_mode(uid)][1]
    doc = docs.get(uid)
    if doc:
        system += docs.context_block(doc)

    if not image and _REALTIME_HINT.search(_norm(text)):
        log.info("Real-vaqt savoli aniqlanib, qidiruvga yuborildi: %s", text)
        answer = llm.chat_with_search(
            state.get_history(uid),
            text,
            system,
        )
    else:
        try:
            answer = llm.chat_multi(
                state.get_history(uid), text, system, image
            )
        except Exception as exc:
            log.error(
                "chat_multi xatosi, oddiy chatga o'tiladi: %s",
                exc,
                exc_info=True,
            )
            answer = llm.chat(
                state.get_history(uid), text, system, image
            )

    state.remember(uid, label or text, answer)
    send_long(m, answer)


def _deliver_image(m, img: bytes, note: str) -> None:
    uid = m.from_user.id
    state.count_image(uid)

    try:
        state.set_image(uid, images.shrink(img))
    except Exception:
        log.warning("Natija rasmini saqlab bo'lmadi", exc_info=True)

    send_photo(m, img)
    state.remember(uid, note, "[Rasm yuborildi]")


def _check_image_quota(m) -> bool:
    if not config.IMAGES_ENABLED:
        reply(m, "🎨 Rasm funksiyasi hozircha sozlanmagan.")
        return False

    if state.images_left(m.from_user.id) <= 0:
        reply(
            m,
            f"🎨 Bugungi rasm limiti ({config.IMAGE_DAILY_LIMIT} ta) tugadi. "
            "Ertaga qayta urinib ko'ring.",
        )
        return False

    return True


def do_draw(m, prompt: str, note: str) -> None:
    if not _check_image_quota(m):
        return

    bot.send_chat_action(m.chat.id, "upload_photo")
    _deliver_image(m, images.make_image(prompt), note)


def do_edit(m, img: bytes | None, prompt: str, note: str) -> None:
    if img is None:
        reply(m, "✏️ Avval tahrirlash uchun rasm yuboring.")
        return

    if not _check_image_quota(m):
        return

    bot.send_chat_action(m.chat.id, "upload_photo")
    _deliver_image(m, images.edit_image(img, prompt), note)


def do_file(m, instruction: str) -> None:
    """Fayl yaratadi yoki yuklangan faylni tahrirlab, hujjat sifatida yuboradi."""
    uid = m.from_user.id

    # FILE_DAILY_LIMIT 0 bo'lsa, limit o'chirilgan deb hisoblanadi.
    if (
        config.FILE_DAILY_LIMIT > 0
        and state.files_left(uid) <= 0
    ):
        reply(
            m,
            f"📄 Bugungi fayl limiti ({config.FILE_DAILY_LIMIT} ta) tugadi. "
            "Ertaga qayta urinib ko'ring.",
        )
        return

    bot.send_chat_action(m.chat.id, "upload_document")
    allow = (
        config.IMAGES_ENABLED
        and state.images_left(uid) > 0
    )

    result = docs.generate(
        state.get_history(uid),
        instruction,
        docs.get(uid),
        allow_images=allow,
    )

    if result.images_added:
        state.count_image(uid)

    docs.store(uid, result.name, result.text)
    send_document(m, result.name, result.data, result.summary)
    state.count_file(uid)
    state.remember(
        uid,
        instruction,
        f"[Fayl yuborildi: {result.name}] {result.summary}",
    )


def route_text(m, text: str) -> None:
    """Matnni chat, rasm yaratish/tahrirlash yoki fayl yaratishga yo'naltiradi."""
    uid = m.from_user.id
    action, prompt = "chat", ""
    last = state.get_image(uid) if config.IMAGES_ENABLED else None
    doc = docs.get(uid)
    t = _norm(text)

    want_image = (
        config.IMAGES_ENABLED
        and looks_like_image_request(text, last is not None)
    )
    want_file = bool(
        _FILE_HINT.search(t)
        or (doc and _DOCEDIT_HINT.search(t))
    )

    if want_image or want_file:
        try:
            action, prompt = llm.classify_vote(
                text,
                last is not None,
                doc is not None,
            )
        except Exception as exc:
            log.warning(
                "Tasniflash xatosi: %s",
                exc,
                exc_info=True,
            )

            # Tasniflagich ishlamasa, aniq buyruqni oddiy chatga yubormaymiz.
            if want_image:
                if last is not None and _EDIT_HINT.search(t):
                    action = "edit"
                elif _DRAW_HINT.search(t):
                    action = "draw"
            elif want_file:
                action = "file"

    if action in ("draw", "edit") and not config.IMAGES_ENABLED:
        action = "chat"

    if action == "draw":
        # Klassifikator promptni o'zgartirib yuborishi mumkin.
        # Asl foydalanuvchi so'rovidan prompt tayyorlaymiz.
        faithful_prompt = llm.refine_prompt(text)
        do_draw(m, faithful_prompt, text)

    elif action == "edit":
        faithful_prompt = llm.refine_prompt(text)
        do_edit(
            m,
            state.get_image(uid),
            faithful_prompt,
            text,
        )

    elif action == "file":
        do_file(m, text)

    else:
        do_chat(m, text)


def handle_image(m, file_id: str, caption: str | None) -> None:
    uid = m.from_user.id

    try:
        info = bot.get_file(file_id)
        raw = bot.download_file(info.file_path)
        img = images.shrink(raw)
    except Exception as exc:
        log.warning(
            "Rasmni yuklab/o'qib bo'lmadi: %s",
            exc,
            exc_info=True,
        )
        reply(m, "🖼 Rasmni o'qib bo'lmadi. JPG yoki PNG formatda qayta yuboring.")
        return

    state.set_image(uid, img)
    caption = (caption or "").strip()

    if caption and config.IMAGES_ENABLED:
        if caption.lower().startswith("/tahrir"):
            instr = caption.partition(" ")[2].strip()
            if not instr:
                reply(m, "Nima o'zgartirish kerakligini yozing: /tahrir fonni tun qil")
                return

            do_edit(m, img, llm.refine_prompt(instr), caption)
            return

        if _EDIT_HINT.search(_norm(caption)):
            do_edit(
                m,
                img,
                llm.refine_prompt(caption),
                caption,
            )
            return

        try:
            action, prompt = llm.classify_vote(caption, True)
        except Exception as exc:
            log.warning(
                "Rasm caption tasnifi xatosi: %s",
                exc,
                exc_info=True,
            )
            action, prompt = "chat", ""

        if action == "edit":
            do_edit(
                m,
                img,
                llm.refine_prompt(caption),
                caption,
            )
            return

        if action == "draw":
            do_draw(
                m,
                llm.refine_prompt(caption),
                caption,
            )
            return

    question = caption or "Bu rasmda nima bor? Batafsil tushuntirib ber."
    do_chat(
        m,
        question,
        image=img,
        label="[Rasm yuborildi] " + question,
    )


# ---------------------------------------------------------------- buyruqlar

@bot.message_handler(commands=["id"], chat_types=["private"])
@protected
def cmd_id(m):
    reply(m, f"Sizning Telegram ID'ingiz: {m.from_user.id}")


@bot.message_handler(commands=["start"], chat_types=["private"])
@protected
def cmd_start(m):
    reply(
        m,
        welcome_text(m.from_user),
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


@bot.message_handler(commands=["help"], chat_types=["private"])
@protected
def cmd_help(m):
    reply(m, HELP, parse_mode="HTML", reply_markup=main_menu())


@bot.message_handler(commands=["clear"], chat_types=["private"])
@protected
def cmd_clear(m):
    clear_all(m.from_user.id)
    reply(m, "🧹 Suhbat tozalandi. Yangi mavzuni boshlashingiz mumkin!")


@bot.message_handler(commands=["mode"], chat_types=["private"])
@protected
def cmd_mode(m):
    reply(
        m,
        "🎭 <b>Javob rejimini tanlang:</b>",
        parse_mode="HTML",
        reply_markup=modes_menu(state.get_mode(m.from_user.id)),
    )


@bot.message_handler(commands=["rasm"], chat_types=["private"])
@protected
def cmd_draw(m):
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        reply(m, "Tavsif yozing: /rasm quyosh botayotgan dengiz")
        return

    if not _check_image_quota(m):
        return

    # Asl tavsif to'g'ridan-to'g'ri prompt tuzishga beriladi.
    do_draw(m, llm.refine_prompt(text), text)


@bot.message_handler(commands=["fayl"], chat_types=["private"])
@protected
def cmd_file(m):
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        reply(
            m,
            "Qanday fayl kerakligini yozing:\n"
            "/fayl 5 ta mijoz uchun narxlar jadvali xlsx\n"
            "/fayl sun'iy intellekt haqida 8 slaydli taqdimot\n\n"
            "Yoki avval fayl yuboring, so'ng /fayl tagiga o'zgarishni yozing.",
        )
        return

    do_file(m, text)


@bot.message_handler(commands=["tahrir"], chat_types=["private"])
@protected
def cmd_edit(m):
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        reply(
            m,
            "Nima o'zgartirish kerakligini yozing: /tahrir fonni tun qil\n"
            "(avval rasm yuborgan bo'lishingiz kerak)",
        )
        return

    img = state.get_image(m.from_user.id)
    if img is None:
        reply(m, "✏️ Avval tahrirlash uchun rasm yuboring.")
        return

    if not _check_image_quota(m):
        return

    do_edit(m, img, llm.refine_prompt(text), text)


# ---------------------------------------------------------------- tugmalar

def _edit_message(chat_id: int, msg_id: int, text: str, markup) -> None:
    try:
        bot.edit_message_text(
            text,
            chat_id,
            msg_id,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except ApiTelegramException as exc:
        if "not modified" not in str(exc).lower():
            raise


def _edit_markup(chat_id: int, msg_id: int, markup) -> None:
    try:
        bot.edit_message_reply_markup(
            chat_id,
            msg_id,
            reply_markup=markup,
        )
    except ApiTelegramException as exc:
        if "not modified" not in str(exc).lower():
            raise


@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    uid = call.from_user.id
    toast = None

    try:
        if not is_allowed(uid):
            toast = "⛔ Ruxsat yo'q"

        elif call.message is not None:
            chat_id = call.message.chat.id
            msg_id = call.message.message_id
            data = call.data or ""

            if data == "menu:modes":
                _edit_message(
                    chat_id,
                    msg_id,
                    "🎭 <b>Javob rejimini tanlang:</b>",
                    modes_menu(state.get_mode(uid)),
                )

            elif data.startswith("mode:"):
                key = data.split(":", 1)[1]
                if key in MODES:
                    state.set_mode(uid, key)
                    _edit_markup(chat_id, msg_id, modes_menu(key))
                    toast = f"Rejim: {MODES[key][0]}"

            elif data == "menu:clear":
                clear_all(uid)
                toast = "🧹 Suhbat tozalandi"

            elif data == "menu:help":
                _edit_message(chat_id, msg_id, HELP, main_menu())

            elif data == "menu:back":
                _edit_message(
                    chat_id,
                    msg_id,
                    welcome_text(call.from_user),
                    main_menu(),
                )

    except Exception:
        log.exception("Callback xatosi")

    finally:
        try:
            bot.answer_callback_query(call.id, toast)
        except Exception:
            pass


# ---------------------------------------------------------------- xabar turlari

@bot.message_handler(
    content_types=["text"],
    func=lambda m: not (m.text or "").startswith("/"),
    chat_types=["private"],
)
@protected
def on_text(m):
    route_text(m, m.text)


@bot.message_handler(content_types=["photo"], chat_types=["private"])
@protected
def on_photo(m):
    handle_image(m, m.photo[-1].file_id, m.caption)


@bot.message_handler(
    content_types=["document"],
    func=lambda m: bool(
        m.document and (m.document.mime_type or "").startswith("image/")
    ),
    chat_types=["private"],
)
@protected
def on_image_document(m):
    if (m.document.file_size or 0) > 10 * 1024 * 1024:
        reply(m, "🖼 Rasm juda katta (10 MB dan oshmasin).")
        return

    handle_image(m, m.document.file_id, m.caption)


@bot.message_handler(content_types=["document"], chat_types=["private"])
@protected
def on_file_document(m):
    doc = m.document

    if (doc.file_size or 0) > docs.MAX_FILE_BYTES:
        reply(
            m,
            f"📄 Fayl juda katta "
            f"(ko'pi bilan {docs.MAX_FILE_BYTES // (1024 * 1024)} MB).",
        )
        return

    name = doc.file_name or "fayl"
    docs.precheck(name, doc.mime_type or "")

    bot.send_chat_action(m.chat.id, "typing")
    info = bot.get_file(doc.file_id)
    raw = bot.download_file(info.file_path)
    text, truncated = docs.extract(name, raw, doc.mime_type or "")

    uid = m.from_user.id
    docs.store(uid, name, text, truncated)

    if truncated:
        reply(
            m,
            f"📄 «{name}» uzun, shuning uchun faqat dastlabki "
            f"~{docs.MAX_TEXT_CHARS} belgisi o'qildi.",
        )

    caption = (m.caption or "").strip()

    if not caption:
        do_chat(
            m,
            f"Men «{name}» faylini yukladim. Uning mazmunini 3-5 gapda "
            "qisqacha tushuntir va nimalar qila olishingni "
            "(savol-javob, tahrirlash, tarjima, xulosa, boshqa formatga "
            "o'tkazish) bir gapda ayt.",
            label=f"[Fayl yuklandi] {name}",
        )
        return

    action = "chat"
    try:
        action, _ = llm.classify_vote(caption, False, True)
    except Exception as exc:
        log.warning(
            "Fayl caption tasniflash xatosi: %s",
            exc,
            exc_info=True,
        )

    if action == "file":
        do_file(m, caption)
    else:
        do_chat(m, caption, label=f"[Fayl: {name}] {caption}")


@bot.message_handler(content_types=["voice"], chat_types=["private"])
@protected
def on_voice(m):
    if (m.voice.duration or 0) > config.VOICE_MAX_SECONDS:
        reply(
            m,
            f"🎤 Ovozli xabar juda uzun "
            f"(ko'pi bilan {config.VOICE_MAX_SECONDS} soniya).",
        )
        return

    bot.send_chat_action(m.chat.id, "typing")
    info = bot.get_file(m.voice.file_id)
    text = llm.transcribe(bot.download_file(info.file_path))

    if not text:
        reply(m, "🎤 Ovozni tushunib bo'lmadi, qayta yozib ko'ring.")
        return

    reply(m, "🎤 " + text[:500])
    route_text(m, text)


@bot.message_handler(
    content_types=["video", "audio", "sticker", "video_note", "animation"],
    chat_types=["private"],
)
def on_other(m):
    if is_allowed(m.from_user.id):
        reply(
            m,
            "📝 Hozircha matn, rasm, ovozli xabar va hujjatlarni "
            "(PDF, Word, Excel...) tushunaman.",
        )


# ---------------------------------------------------------------- ishga tushirish

class _Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def start_health_server() -> None:
    """Northflank health-check uchun yengil HTTP server."""
    if not config.HEALTH_PORT:
        return

    try:
        server = ThreadingHTTPServer(
            ("0.0.0.0", config.HEALTH_PORT),
            _Health,
        )
    except OSError as exc:
        log.warning(
            "Health server ishga tushmadi: %s",
            exc,
            exc_info=True,
        )
        return

    threading.Thread(
        target=server.serve_forever,
        daemon=True,
    ).start()
    log.info("Health server: port %s", config.HEALTH_PORT)


def _shutdown(signum, _frame) -> None:
    log.info("Signal %s — bot to'xtatilmoqda...", signum)
    threading.Timer(8, lambda: os._exit(0)).start()
    bot.stop_polling()


def main() -> None:
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    start_health_server()

    bot.remove_webhook()
    me = bot.get_me()

    try:
        bot.set_my_commands([
            types.BotCommand("start", "Botni boshlash"),
            types.BotCommand("mode", "Javob rejimi"),
            types.BotCommand("clear", "Suhbatni tozalash"),
            types.BotCommand("rasm", "Rasm chizish"),
            types.BotCommand("tahrir", "Rasmni tahrirlash"),
            types.BotCommand("fayl", "Fayl yoki taqdimot yaratish"),
            types.BotCommand("help", "Yordam"),
        ])
    except Exception:
        log.warning(
            "Buyruqlar ro'yxatini o'rnatib bo'lmadi",
            exc_info=True,
        )

    log.info(
        "@%s ishga tushdi | chat: %s | vision: %s | rasm: %s | "
        "zaxira Gemini: %s | kirish: %s",
        me.username,
        config.CHAT_MODEL,
        config.VISION_MODEL,
        "yoqilgan" if config.IMAGES_ENABLED else "o'chiq",
        "bor" if config.GEMINI_API_KEY else "yo'q",
        f"{len(config.ALLOWED_USERS)} ta ID"
        if config.ALLOWED_USERS
        else "hamma uchun ochiq",
    )

    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
