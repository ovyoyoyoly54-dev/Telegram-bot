"""AI Telegram bot: chat, rasm tushunish, rasm chizish/tahrirlash, ovozli xabarlar,
fayllarni o'qish / yaratish / tahrirlash (PDF, Word, Excel, PowerPoint, matn va kod).
Groq (asosiy) + Gemini (zaxira) + Cloudflare (rasm). Northflank'da long-polling bilan ishlaydi."""
import functools
import html
import io
import logging
import os
import re
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

import config
import deck as deckmod
import files
import images
import llm
import presenton
import state
from formatting import split_text, to_telegram_html
from keyboards import MODES, main_menu, modes_menu

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
log = logging.getLogger("bot")

bot = telebot.TeleBot(config.TELEGRAM_TOKEN, parse_mode=None, threaded=True,
                      num_threads=config.WORKERS)

WELCOME = (
    "👋 <b>Salom, {name}!</b>\n\n"
    "Men aqlli AI yordamchiman 🤖\n"
    "• Savol yozing — javob beraman\n"
    "• 🎤 Ovozli xabar yuboring — tushunaman\n"
    "• 🖼 Rasm yuboring — nima borligini aytaman\n"
    "• «dengiz ustida quyosh botayotgan rasm chiz» deb yozing — chizaman\n"
    "• Rasm yuborib, tagiga «fonni tun qil» deb yozing — tahrirlayman\n"
    "• 📎 Fayl yuboring (PDF, Word, Excel, PowerPoint, matn, kod) — o'qib beraman\n"
    "• Fayl tagiga «xatolarni to'g'rila» deb yozing — tahrirlab qaytaraman\n"
    "• «xarajatlar jadvalini excelda qilib ber» deb yozing — fayl yarataman\n"
    "• «AI haqida 10 slaydli taqdimot qil» deb yozing — chiroyli taqdimot (PPTX) yarataman, "
    "«3-slaydni qisqartir» desangiz, aynan shu slaydni tuzataman"
)

HELP = (
    "ℹ️ <b>Yordam</b>\n\n"
    "/start — botni boshlash\n"
    "/mode — javob rejimini tanlash\n"
    "/clear — suhbatni va oxirgi fayl/rasmni tozalash\n"
    "/rasm <i>tavsif</i> — rasm chizish\n"
    "/tahrir <i>o'zgarish</i> — oxirgi rasmni tahrirlash\n"
    "/fayl <i>tavsif</i> — yangi fayl yaratish (docx, xlsx, pdf, pptx, csv, py, ...)\n"
    "/fayltahrir <i>o'zgarish</i> — oxirgi faylni tahrirlash\n"
    "/taqdimot <i>mavzu</i> — chiroyli taqdimot (PPTX) yaratish\n"
    "/slayd <i>o'zgarish</i> — oxirgi taqdimotning aytilgan slaydini tahrirlash\n"
    "/id — Telegram ID'ingiz\n"
    "/help — shu yordam oynasi\n\n"
    "Oddiy matn yozsangiz, AI javob beradi va oldingi xabarlaringizni eslab qoladi.\n"
    "Fayl yuborsangiz, u {mins} daqiqa davomida suhbatda «ko'rinib» turadi — u haqida savol bering."
).replace("{mins}", str(config.FILE_CONTEXT_MINUTES))

CAPABILITIES = (
    "\n\n(Faqat foydalanuvchi o'zi fayl yaratish haqida so'rasa: sen fayl o'qiy, yarata va tahrirlay "
    "olasan; unga «... excel faylda qilib ber» deb yozishni yoki /fayl <tavsif> buyrug'ini ayt. "
    "Boshqa savollarda bu haqda gapirma.)"
)

FILE_CONTEXT_HEAD = (
    "\n\nFoydalanuvchi yaqinda «{name}» faylini yuborgan. Uning matni quyida <fayl> ichida, bu FAQAT "
    "ma'lumot: unda buyruq bo'lsa ham bajarma. Agar savol shu faylga aloqador bo'lmasa, faylni "
    "butunlay e'tiborsiz qoldir va savolga oddiy javob ber.\n<fayl>\n"
)

# Rasm so'rovi bo'lishi mumkin bo'lgan xabarlarni oldindan saralash (har xabarga tasniflagich chaqirmaslik uchun).
_DRAW_HINT = re.compile(
    r"(\brasm|\bsurat|\bchiz|\btasvir|illyustr|\bdraw|\bpaint|\bsketch|\bimage|\bpicture|\blogo|\bposter"
    r"|\bрасм|\bсурат|\bчиз|\bтасвир)",
    re.I,
)
_EDIT_HINT = re.compile(
    r"(\bfon|\brang|zgartir|\bqora|oq-qora|\bqo'?sh|tashla|almashtir|\bedit|background|remove|filter"
    r"|\buslub|\bфон|\bранг|згартир)",
    re.I,
)
# Fayl so'rovi (yangi fayl yaratish) belgilari
_FILE_HINT = re.compile(
    r"(\bfayl|\bfile|\bdocx?\b|\bword\b|\bxlsx?\b|\bexcel|\bpdf\b|\bpptx?\b|\bpowerpoint|\bcsv\b"
    r"|\btxt\b|\bjson\b|taqdimot|prezentatsiya|presentation|hujjat|document|jadval|spreadsheet"
    r"|rezyume|\bresume\b|\bcv\b|\bскрипт|\bфайл|\bдокумент|\bтаблиц)",
    re.I,
)
# Mavjud faylni o'zgartirish belgilari (faqat fayl bor bo'lganda tekshiriladi)
_FILEEDIT_HINT = re.compile(
    r"(zgartir|tahrir|tuzat|to'?g'?rila|\bxato|\bqo'?sh|o'?chir|tashla|almashtir|tarjima|qayta yoz"
    r"|yangila|qisqartir|kengaytir|format|\bedit|\bfix|\badd\b|\bremove|\bchange|translate|rewrite|update"
    r"|\bисправ|\bдобав|\bперевед|\bизмен|\bудал)",
    re.I,
)


def _norm(text: str) -> str:
    return text.replace("ʻ", "'").replace("‘", "'").replace("’", "'").replace("`", "'")


def looks_like_image_request(text: str, has_image: bool) -> bool:
    t = _norm(text)
    return bool(_DRAW_HINT.search(t) or (has_image and _EDIT_HINT.search(t)))


def looks_like_file_request(text: str, has_file: bool) -> bool:
    t = _norm(text)
    return bool(_FILE_HINT.search(t) or (has_file and _FILEEDIT_HINT.search(t)))


# Aniq "fayl yarat" so'rovi: format/fayl so'zi + yaratish fe'li. Tasniflagich ishlamay qolsa ham
# shunday so'rov oddiy suhbatga tushib ketmasligi uchun zaxira qoida.
_FORMAT_WORD = re.compile(
    r"(\bfayl|\bfile|\bdocx?\b|\bword\b|\bxlsx?\b|\bexcel|\bpdf\b|\bpptx?\b|\bpowerpoint|\bcsv\b|\btxt\b"
    r"|taqdimot|prezentatsiya|presentation|\bjadval|\bфайл|\bворд|\bэксель|\bпрезентац)",
    re.I,
)
_CREATE_VERB = re.compile(
    r"(yarat|\bqil\b|qilib|tayyorla|\byoz\b|yozib|chiqar|\bber\b|\btuz\b|tuzib|\bmake\b|\bcreate|generate"
    r"|\bwrite\b|\bbuild\b|\bsdelay|\bсделай|\bсоздай|\bнапиши|\bподготов)",
    re.I,
)


def strong_file_request(text: str) -> bool:
    t = _norm(text)
    return bool(_FORMAT_WORD.search(t) and _CREATE_VERB.search(t))


# ---------------------------------------------------------------- yuborish yordamchilari
def _send(chat_id: int, text: str, reply_to: int | None = None, **kw):
    try:
        return bot.send_message(chat_id, text, reply_to_message_id=reply_to, **kw)
    except ApiTelegramException as exc:
        if reply_to and "repl" in str(exc).lower():  # asl xabar o'chirilgan bo'lishi mumkin
            return bot.send_message(chat_id, text, **kw)
        raise


def reply(m, text: str, **kw):
    return _send(m.chat.id, text, m.message_id, **kw)


def send_long(m, text: str) -> None:
    """Uzun javobni bo'laklab, chiroyli (HTML) formatda yuboradi; HTML xato bo'lsa — oddiy matn."""
    first = True
    for chunk in split_text(text, config.TELEGRAM_MAX_CHARS):
        if not chunk.strip():
            continue
        reply_to = m.message_id if first else None
        first = False
        try:
            _send(m.chat.id, to_telegram_html(chunk), reply_to, parse_mode="HTML")
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


def send_file(m, name: str, data: bytes, caption: str = "") -> None:
    def _go(**kw):
        bio = io.BytesIO(data)
        bio.name = name
        bot.send_document(m.chat.id, bio, caption=caption[:1000] or None, visible_file_name=name, **kw)

    try:
        _go(reply_to_message_id=m.message_id)
    except ApiTelegramException as exc:
        if "repl" in str(exc).lower():
            _go()
        else:
            raise


def welcome_text(user) -> str:
    return WELCOME.format(name=html.escape(user.first_name or "do'st"))


# ---------------------------------------------------------------- himoya (ruxsat, limit, xatolar)
def is_allowed(uid: int) -> bool:
    return not config.ALLOWED_USERS or uid in config.ALLOWED_USERS


def _who(user) -> str:
    name = " ".join(p for p in (user.first_name, user.last_name) if p) or "ismsiz"
    uname = f"@{user.username}" if user.username else "username yo'q"
    return f"{name} ({uname}) | ID: {user.id}"


def _track_user(user) -> None:
    """Botga yozgan odamni qayd qiladi; yangi bo'lsa — logga yozadi va adminlarga xabar beradi."""
    try:
        allowed = is_allowed(user.id)
        name = " ".join(p for p in (user.first_name, user.last_name) if p) or "ismsiz"
        if state.track(user.id, name, user.username or "", allowed):
            log.info("Yangi foydalanuvchi: %s%s", _who(user), "" if allowed else " [RUXSATSIZ]")
            for admin in config.ADMIN_USERS:
                if admin != user.id:
                    try:
                        bot.send_message(admin, "🆕 Botga yangi odam yozdi:\n" + _who(user)
                                         + ("" if allowed else "\n⛔ Ruxsati yo'q (ALLOWED_USERS)"))
                    except Exception:  # noqa: BLE001 — admin botni boshlamagan bo'lishi mumkin
                        pass
    except Exception:  # noqa: BLE001 — qayd qilish asosiy ishni to'xtatmasin
        log.warning("Foydalanuvchini qayd qilib bo'lmadi")


def protected(fn):
    """Ruxsat → tezlik limiti → bir vaqtda bitta so'rov → xatolarni foydalanuvchiga tushunarli qilish."""

    @functools.wraps(fn)
    def wrapper(m, *args, **kwargs):
        uid = m.from_user.id
        _track_user(m.from_user)
        if not is_allowed(uid):
            reply(m, f"⛔ Kechirasiz, bu bot yopiq. ID'ingiz: {uid}")
            return
        wait = state.rate_wait(uid)
        if wait > 0:
            reply(m, f"⏳ Iltimos, biroz sekinroq yozing ({wait:.0f} soniyadan keyin urinib ko'ring).")
            return
        with state.user_slot(uid) as ok:
            if not ok:
                reply(m, "⏳ Oldingi so'rovingiz hali bajarilmoqda, biroz kuting.")
                return
            try:
                fn(m, *args, **kwargs)
            except (llm.LLMError, images.ImageError, files.FileError, deckmod.DeckError) as exc:
                log.warning("Foydalanuvchiga xato: %s", exc)
                _safe_reply(m, f"⚠️ {exc}")
            except Exception:  # noqa: BLE001
                log.exception("Kutilmagan xato")
                _safe_reply(m, "❌ Kutilmagan xatolik yuz berdi. Keyinroq urinib ko'ring.")

    return wrapper


def _safe_reply(m, text: str) -> None:
    try:
        reply(m, text)
    except Exception:  # noqa: BLE001
        log.exception("Xato xabarini yuborib bo'lmadi")


# ---------------------------------------------------------------- asosiy mantiq
def file_context(uid: int) -> str:
    """Oxirgi fayl yaqinda ishlatilgan bo'lsa, uning matnini tizim ko'rsatmasiga qo'shadi."""
    f = state.get_file(uid)
    if not f or state.recent_kind(uid) != "file":
        return ""
    if time.monotonic() - f["ts"] > config.FILE_CONTEXT_MINUTES * 60:
        return ""
    return FILE_CONTEXT_HEAD.replace("{name}", f["name"]) + f["text"] + "\n</fayl>"


def do_chat(m, text: str, image: bytes | None = None, label: str | None = None) -> None:
    uid = m.from_user.id
    bot.send_chat_action(m.chat.id, "typing")
    system = MODES[state.get_mode(uid)][1] + CAPABILITIES
    if image is None:
        system += file_context(uid)
    answer = llm.chat(state.get_history(uid), text, system, image)
    state.remember(uid, label or text, answer)
    send_long(m, answer)


def _deliver_image(m, img: bytes, note: str) -> None:
    uid = m.from_user.id
    state.count_image(uid)
    try:
        state.set_image(uid, images.shrink(img))
    except Exception:  # noqa: BLE001 — keyingi tahrir uchun saqlab bo'lmasa ham, rasm yuborilsin
        log.warning("Natija rasmini kichraytirib bo'lmadi")
    send_photo(m, img)  # tarixga yozilmaydi: "[Rasm yuborildi]" kabi yozuvlarga model taqlid qilib qolardi


def _check_image_quota(m) -> bool:
    if not config.IMAGES_ENABLED:
        reply(m, "🎨 Rasm funksiyasi hozircha sozlanmagan.")
        return False
    if state.images_left(m.from_user.id) <= 0:
        reply(m, f"🎨 Bugungi rasm limiti ({config.IMAGE_DAILY_LIMIT} ta) tugadi. Ertaga qayta urinib ko'ring.")
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


# ---------------------------------------------------------------- fayllar
def _check_file_quota(m) -> bool:
    if state.files_left(m.from_user.id) <= 0:
        reply(m, f"📎 Bugungi fayl limiti ({config.FILE_DAILY_LIMIT} ta) tugadi. Ertaga qayta urinib ko'ring.")
        return False
    return True


def _deliver_file(m, name: str, content: str, summary: str, note: str, edited: bool) -> None:
    uid = m.from_user.id
    try:
        data = files.build_file(name, content)
    except Exception as exc:  # noqa: BLE001
        log.exception("Faylni qurib bo'lmadi: %s", name)
        raise files.FileError("Faylni yaratib bo'lmadi. Boshqa formatda so'rab ko'ring.") from exc
    if len(data) > 49 * 1024 * 1024:
        raise files.FileError("Natija fayli Telegram uchun juda katta.")
    state.count_file(uid)
    clipped, truncated = files.clip(content)
    state.set_file(uid, name, clipped, truncated)  # keyingi tahrir shundan davom etadi
    caption = "📎 " + (summary or name)
    if edited and files.ext_of(name) in files.BUILD_EXTS:
        caption += "\n\nℹ️ Fayl qayta yaratildi: rasmlar va murakkab dizayn saqlanmasligi mumkin."
    send_file(m, name, data, caption)
    state.remember(uid, note, summary or "Tayyor, faylni yubordim.")  # oddiy gap: model taqlid qilib adashmasin


_PRESENTATION = re.compile(
    r"(taqdimot|prezentatsiya|presentation|\bpptx?\b|powerpoint|\bslayd|\bslide|презентац|слайд)", re.I)


def do_presentation(m, request: str) -> bool:
    """Presenton orqali chiroyli taqdimot. Muvaffaqiyatli bo'lsa True; ishlamasa False (oddiy yaratuvchiga o'tiladi)."""
    uid = m.from_user.id
    export = "pdf" if re.search(r"\bpdf\b", request, re.I) else "pptx"
    reply(m, "🎞 Taqdimot tayyorlanmoqda, bu 1–3 daqiqa olishi mumkin...")
    bot.send_chat_action(m.chat.id, "upload_document")
    try:
        name, data = presenton.generate(request, export)
    except presenton.PresentonError as exc:
        log.warning("Presenton ishlamadi: %s", exc)
        reply(m, f"ℹ️ {exc} Oddiy usulda yaratyapman...")
        return False
    state.count_file(uid)
    send_file(m, name, data, "🎞 Taqdimot tayyor")
    state.remember(uid, request, "Taqdimot tayyor, faylni yubordim.")
    return True


_DECK_NEW = re.compile(r"(\byangi\b|\bboshqa\b|\bnew\b|\bновую?\b)", re.I)
_DECK_EDIT = re.compile(r"(slayd|slide|taqdimot|prezent|mavzu|dizayn|diagramma|grafik|sarlavha|слайд|презент)", re.I)

DECK_TIPS = (
    "✏️ Tahrirlash uchun shunchaki yozing, masalan:\n"
    "• «3-slaydni qisqartir»\n"
    "• «5-slaydga diagramma qo'sh»\n"
    "• «2-slayddagi rasmni dengiz manzarasiga almashtir»\n"
    "• «mavzuni ocean qil» (mavzular: midnight, ocean, sunset, forest, royal, minimal)\n"
    "• «hammasini inglizchaga tarjima qil»\n"
    "Fayl PowerPoint, Google Slides va Canva'da ochiladi."
)


def _deck_images(d: dict, cache: dict) -> dict:
    """Taqdimotdagi yangi rasm promptlari uchun AI rasm chizadi (eskilari qayta chizilmaydi)."""
    if not config.IMAGES_ENABLED or config.DECK_IMAGES <= 0:
        return cache
    todo = [p for p in deckmod.image_prompts(d) if p not in cache][: config.DECK_IMAGES]
    if not todo:
        return cache

    def one(prompt: str):
        try:
            raw = images.make_image(prompt + ", high quality, no text, no letters, no watermark")
            return prompt, images.shrink(raw)
        except Exception as exc:  # noqa: BLE001 — rasm chiqmasa ham taqdimot tayyor bo'laveradi
            log.warning("Taqdimot rasmi chizilmadi: %s", exc)
            return prompt, None

    with ThreadPoolExecutor(max_workers=3) as pool:
        for prompt, img in pool.map(one, todo):
            if img:
                cache[prompt] = img
    return cache


def _send_deck(m, d: dict, cache: dict, caption: str) -> None:
    try:
        data = deckmod.build_pptx(d, cache)
    except Exception as exc:  # noqa: BLE001
        log.exception("Taqdimotni qurib bo'lmadi")
        raise deckmod.DeckError("Taqdimot faylini yasab bo'lmadi, qayta urinib ko'ring.") from exc
    if len(data) > 49 * 1024 * 1024:
        raise files.FileError("Taqdimot fayli Telegram uchun juda katta.")
    name = files.safe_name(d["title"].replace(" ", "_"), "taqdimot")[:50] + ".pptx"
    send_file(m, name, data, caption)


def do_deck_create(m, request: str, source: str | None = None) -> None:
    uid = m.from_user.id
    reply(m, "🎞 Taqdimot tayyorlanmoqda, 30–90 soniya kuting...")
    bot.send_chat_action(m.chat.id, "upload_document")
    d = llm.generate_deck(request, source)
    cache = _deck_images(d, {})
    state.set_deck(uid, d, cache)
    state.count_file(uid)
    _send_deck(m, d, cache, f"🎞 {d['title']} — {len(d['slides'])} slayd, mavzu: {d['theme']}")
    reply(m, "📋 Slaydlar:\n" + deckmod.outline(d) + "\n\n" + DECK_TIPS)
    state.remember(uid, request, f"Taqdimot tayyor: «{d['title']}», {len(d['slides'])} slayd.")


def do_deck_edit(m, request: str) -> None:
    uid = m.from_user.id
    cur = state.get_deck(uid)
    if cur is None:
        reply(m, "🎞 Avval taqdimot yarating: /taqdimot <mavzu>")
        return
    if not _check_file_quota(m):
        return
    bot.send_chat_action(m.chat.id, "upload_document")
    new, done, summary = llm.edit_deck(cur["deck"], request)
    cache = _deck_images(new, cur["images"])
    state.set_deck(uid, new, cache)
    state.count_file(uid)
    _send_deck(m, new, cache, "✏️ " + (summary or "; ".join(done))[:900])
    state.remember(uid, request, summary or "; ".join(done))


def deck_edit_wanted(uid: int, text: str) -> bool:
    """Yaqinda taqdimot yaratilgan bo'lsa va xabar uni o'zgartirishga o'xshasa — True."""
    cur = state.get_deck(uid)
    if not cur or state.recent_kind(uid) != "deck":
        return False
    if time.monotonic() - cur["ts"] > config.DECK_CONTEXT_MINUTES * 60:
        return False
    t = _norm(text)
    if _DECK_NEW.search(t) and _PRESENTATION.search(t):
        return False
    return bool(_DECK_EDIT.search(t) or _FILEEDIT_HINT.search(t))


def do_file_create(m, request: str) -> None:
    if not _check_file_quota(m):
        return
    if _PRESENTATION.search(_norm(request)):
        if config.PRESENTON_URL:
            if do_presentation(m, request):
                return
        else:
            try:
                do_deck_create(m, request)
                return
            except deckmod.DeckError as exc:
                log.warning("Taqdimot dvigateli ishlamadi: %s", exc)
                reply(m, "ℹ️ Chiroyli taqdimot chiqmadi, oddiy usulda yaratyapman...")
    bot.send_chat_action(m.chat.id, "upload_document")
    name, content, summary = llm.generate_file(request)
    _deliver_file(m, name, content, summary, request, edited=False)


def do_file_edit(m, request: str) -> None:
    uid = m.from_user.id
    f = state.get_file(uid)
    if f is None:
        reply(m, "📎 Avval tahrirlash uchun fayl yuboring yoki /fayl bilan yarating.")
        return
    if f["truncated"]:
        reply(m, f"📎 «{f['name']}» juda katta — to'liq tahrirlab bo'lmaydi (ma'lumot kesilib qolardi). "
                 "Kichikroq qismini yuboring.")
        return
    if not _check_file_quota(m):
        return
    if files.ext_of(f["name"]) == "pptx" and not config.PRESENTON_URL:
        # Yuborilgan PowerPoint: mazmuni asosida chiroyli taqdimot quriladi, keyin slaydma-slayd tahrirlanadi
        do_deck_create(m, request, source=f["text"])
        return
    bot.send_chat_action(m.chat.id, "upload_document")
    name, content, summary = llm.generate_file(request, f["name"], f["text"])
    _deliver_file(m, name, content, summary, request, edited=True)


def handle_document(m, doc, caption: str | None) -> None:
    """Yuborilgan faylni o'qiydi; tagidagi yozuvga qarab: savol-javob, tahrirlash yoki yangi fayl."""
    uid = m.from_user.id
    name = files.safe_name(doc.file_name or "fayl")
    if (doc.file_size or 0) > config.FILE_MAX_MB * 1024 * 1024:
        reply(m, f"📎 Fayl juda katta ({config.FILE_MAX_MB} MB dan oshmasin).")
        return
    bot.send_chat_action(m.chat.id, "typing")
    try:
        info = bot.get_file(doc.file_id)
        raw = bot.download_file(info.file_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("Faylni yuklab bo'lmadi: %s", exc)
        reply(m, "📎 Faylni yuklab bo'lmadi (Telegram botlar uchun 20 MB limit bor). Qayta yuboring.")
        return
    text, truncated = files.clip(files.extract_text(raw, name))  # FileError bo'lsa — protected ushlaydi
    state.set_file(uid, name, text, truncated)
    if truncated:
        reply(m, f"ℹ️ Fayl katta bo'lgani uchun faqat boshidagi ~{config.FILE_MAX_CHARS} belgi o'qildi.")

    caption = (caption or "").strip()
    action = "chat"
    if caption:
        try:
            action, _ = llm.classify(caption, False, True, "file")
        except Exception as exc:  # noqa: BLE001 — tasniflash ishlamasa, oddiy savol deb olamiz
            log.warning("Tasniflash xatosi: %s", exc)
    if action == "fileedit":
        do_file_edit(m, caption)
        return
    if action == "file":
        do_file_create(m, caption)
        return
    question = caption or "Bu fayl nimadan iborat? Qisqa xulosa qilib ber."
    do_chat(m, question, label=f"({name} fayli haqida) {question}")


# ---------------------------------------------------------------- yo'naltirish
def route_text(m, text: str) -> None:
    """Matnni tasniflab: oddiy suhbat, rasm chizish/tahrirlash yoki fayl yaratish/tahrirlashga yo'naltiradi."""
    uid = m.from_user.id
    if deck_edit_wanted(uid, text):
        log.info("Yo'naltirish: deckedit | %.60s", text)
        do_deck_edit(m, text)
        return
    has_img = config.IMAGES_ENABLED and state.get_image(uid) is not None
    has_file = state.get_file(uid) is not None
    action, prompt = "chat", ""
    hint_img = config.IMAGES_ENABLED and looks_like_image_request(text, has_img)
    if hint_img or looks_like_file_request(text, has_file):
        try:
            action, prompt = llm.classify(text, has_img, has_file, state.recent_kind(uid))
        except Exception as exc:  # noqa: BLE001 — tasniflash ishlamasa, zaxira qoidaga o'tamiz
            log.warning("Tasniflash xatosi: %s", exc)
        log.info("Yo'naltirish: %s | %.60s", action, text)
        # Zaxira: tasniflagich "chat" desa yoki ishlamasa-yu, so'rov aniq fayl yaratish bo'lsa
        if action == "chat" and strong_file_request(text):
            if has_file and _FILEEDIT_HINT.search(_norm(text)) and not _FORMAT_WORD.search(_norm(text)):
                action = "fileedit"
            else:
                action = "file"
            log.info("Zaxira qoida: %s", action)
    if action == "draw":
        do_draw(m, prompt, text)
    elif action == "edit":
        do_edit(m, state.get_image(uid), prompt, text)
    elif action == "file":
        do_file_create(m, text)
    elif action == "fileedit":
        do_file_edit(m, text)
    else:
        do_chat(m, text)


def handle_image(m, file_id: str, caption: str | None) -> None:
    uid = m.from_user.id
    try:
        info = bot.get_file(file_id)
        raw = bot.download_file(info.file_path)
        img = images.shrink(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("Rasmni yuklab/o'qib bo'lmadi: %s", exc)
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
        try:
            action, prompt = llm.classify(caption, True, False, "image")
        except Exception as exc:  # noqa: BLE001
            log.warning("Tasniflash xatosi: %s", exc)
            action, prompt = "chat", ""
        if action == "edit":
            do_edit(m, img, prompt, caption)
            return
        if action == "draw":
            do_draw(m, prompt, caption)
            return
    question = caption or "Bu rasmda nima bor? Batafsil tushuntirib ber."
    do_chat(m, question, image=img, label="(rasm haqida) " + question)


# ---------------------------------------------------------------- buyruqlar
@bot.message_handler(commands=["id"], chat_types=["private"])
def cmd_id(m):
    reply(m, f"Sizning Telegram ID'ingiz: {m.from_user.id}")


@bot.message_handler(commands=["users"], chat_types=["private"])
def cmd_users(m):
    """Faqat adminlar uchun: botga yozganlar ro'yxati (bot qayta ishga tushganidan beri)."""
    if m.from_user.id not in config.ADMIN_USERS:
        return  # oddiy foydalanuvchiga buyruq borligi sezilmasin
    total, rows = state.list_users(30)
    if not rows:
        reply(m, "Hozircha hech kim yozmagan.")
        return
    tz = timezone(timedelta(hours=config.TZ_HOURS))
    lines = [f"👥 Jami: {total} (oxirgi {len(rows)} tasi, eng yangisi birinchi)\n"]
    for uid, r in rows:
        uname = f"@{r['username']}" if r["username"] else "-"
        last = datetime.fromtimestamp(r["last"], tz).strftime("%d.%m %H:%M")
        lock = "" if r["allowed"] else " ⛔"
        lines.append(f"{r['name']} | {uname} | {uid} | {r['count']} ta | {last}{lock}")
    for chunk in split_text("\n".join(lines), config.TELEGRAM_MAX_CHARS):
        reply(m, chunk)


@bot.message_handler(commands=["start"], chat_types=["private"])
@protected
def cmd_start(m):
    reply(m, welcome_text(m.from_user), parse_mode="HTML", reply_markup=main_menu())


@bot.message_handler(commands=["help"], chat_types=["private"])
@protected
def cmd_help(m):
    reply(m, HELP, parse_mode="HTML", reply_markup=main_menu())


@bot.message_handler(commands=["clear"], chat_types=["private"])
@protected
def cmd_clear(m):
    state.clear_history(m.from_user.id)
    reply(m, "🧹 Suhbat tozalandi. Yangi mavzuni boshlashingiz mumkin!")


@bot.message_handler(commands=["mode"], chat_types=["private"])
@protected
def cmd_mode(m):
    reply(
        m, "🎭 <b>Javob rejimini tanlang:</b>", parse_mode="HTML",
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
    do_draw(m, llm.refine_prompt(text), text)


@bot.message_handler(commands=["tahrir"], chat_types=["private"])
@protected
def cmd_edit(m):
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        reply(m, "Nima o'zgartirish kerakligini yozing: /tahrir fonni tun qil\n(avval rasm yuborgan bo'lishingiz kerak)")
        return
    img = state.get_image(m.from_user.id)
    if img is None:
        reply(m, "✏️ Avval tahrirlash uchun rasm yuboring.")
        return
    if not _check_image_quota(m):
        return
    do_edit(m, img, llm.refine_prompt(text), text)


@bot.message_handler(commands=["fayl"], chat_types=["private"])
@protected
def cmd_file(m):
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        reply(m, "Nima yaratish kerakligini yozing:\n/fayl 10 qatorli xarajatlar jadvali, excel formatida\n"
                 "/fayl mening rezyumem, word formatida\n/fayl telegram bot uchun main.py")
        return
    do_file_create(m, text)


@bot.message_handler(commands=["taqdimot"], chat_types=["private"])
@protected
def cmd_deck(m):
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        reply(m, "Mavzuni yozing:\n/taqdimot sun'iy intellekt haqida 10 slayd\n"
                 "/taqdimot mening startapim uchun investor taqdimoti, ocean mavzusida")
        return
    if not _check_file_quota(m):
        return
    try:
        do_deck_create(m, "Taqdimot: " + text)
    except deckmod.DeckError as exc:
        log.warning("Taqdimot yaratilmadi: %s", exc)
        reply(m, "⚠️ Taqdimotni yaratib bo'lmadi, mavzuni boshqacha yozib qayta urinib ko'ring.")


@bot.message_handler(commands=["slayd"], chat_types=["private"])
@protected
def cmd_slide(m):
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        reply(m, "Nimani o'zgartirish kerakligini yozing:\n/slayd 3-slaydni qisqartir\n"
                 "/slayd mavzuni sunset qil\n(avval taqdimot yaratgan bo'lishingiz kerak)")
        return
    do_deck_edit(m, text)


@bot.message_handler(commands=["fayltahrir"], chat_types=["private"])
@protected
def cmd_file_edit(m):
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        reply(m, "Nima o'zgartirish kerakligini yozing: /fayltahrir 3-bandni qisqartir\n"
                 "(avval fayl yuborgan yoki yaratgan bo'lishingiz kerak)")
        return
    do_file_edit(m, text)


# ---------------------------------------------------------------- tugmalar
def _edit_message(chat_id: int, msg_id: int, text: str, markup) -> None:
    try:
        bot.edit_message_text(text, chat_id, msg_id, parse_mode="HTML", reply_markup=markup)
    except ApiTelegramException as exc:
        if "not modified" not in str(exc).lower():  # xabar o'zgarmagan bo'lsa — xato emas
            raise


def _edit_markup(chat_id: int, msg_id: int, markup) -> None:
    try:
        bot.edit_message_reply_markup(chat_id, msg_id, reply_markup=markup)
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
            chat_id, msg_id = call.message.chat.id, call.message.message_id
            data = call.data or ""
            if data == "menu:modes":
                _edit_message(chat_id, msg_id, "🎭 <b>Javob rejimini tanlang:</b>", modes_menu(state.get_mode(uid)))
            elif data.startswith("mode:"):
                key = data.split(":", 1)[1]
                if key in MODES:
                    state.set_mode(uid, key)
                    _edit_markup(chat_id, msg_id, modes_menu(key))
                    toast = f"Rejim: {MODES[key][0]}"
            elif data == "menu:clear":
                state.clear_history(uid)
                toast = "🧹 Suhbat tozalandi"
            elif data == "menu:help":
                _edit_message(chat_id, msg_id, HELP, main_menu())
            elif data == "menu:back":
                _edit_message(chat_id, msg_id, welcome_text(call.from_user), main_menu())
    except Exception:  # noqa: BLE001
        log.exception("Callback xatosi")
    finally:
        try:
            bot.answer_callback_query(call.id, toast)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- xabar turlari
@bot.message_handler(content_types=["text"], func=lambda m: not (m.text or "").startswith("/"),
                     chat_types=["private"])
@protected
def on_text(m):
    route_text(m, m.text)


@bot.message_handler(content_types=["photo"], chat_types=["private"])
@protected
def on_photo(m):
    handle_image(m, m.photo[-1].file_id, m.caption)


@bot.message_handler(
    content_types=["document"],
    func=lambda m: bool(m.document and (m.document.mime_type or "").startswith("image/")),
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
def on_document(m):
    handle_document(m, m.document, m.caption)


@bot.message_handler(content_types=["voice"], chat_types=["private"])
@protected
def on_voice(m):
    if (m.voice.duration or 0) > config.VOICE_MAX_SECONDS:
        reply(m, f"🎤 Ovozli xabar juda uzun (ko'pi bilan {config.VOICE_MAX_SECONDS} soniya).")
        return
    bot.send_chat_action(m.chat.id, "typing")
    info = bot.get_file(m.voice.file_id)
    text = llm.transcribe(bot.download_file(info.file_path))
    if not text:
        reply(m, "🎤 Ovozni tushunib bo'lmadi, qayta yozib ko'ring.")
        return
    reply(m, "🎤 " + text[:500])
    route_text(m, text)


@bot.message_handler(content_types=["video", "audio", "sticker", "video_note", "animation"],
                     chat_types=["private"])
def on_other(m):
    if is_allowed(m.from_user.id):
        reply(m, "📝 Hozircha matn, rasm, fayl va ovozli xabarlarni tushunaman.")


# ---------------------------------------------------------------- ishga tushirish
class _Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # jim
        pass


def start_health_server() -> None:
    """Northflank health-check uchun yengil HTTP server (HEALTH_PORT/PORT=0 bo'lsa o'chiriladi)."""
    if not config.HEALTH_PORT:
        return
    try:
        server = HTTPServer(("0.0.0.0", config.HEALTH_PORT), _Health)
    except OSError as exc:
        log.warning("Health server ishga tushmadi: %s", exc)
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Health server: port %s", config.HEALTH_PORT)


def _shutdown(signum, _frame) -> None:
    log.info("Signal %s — bot to'xtatilmoqda...", signum)
    threading.Timer(8, lambda: os._exit(0)).start()  # to'xtamasa, majburan chiqamiz
    bot.stop_polling()


def main() -> None:
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    start_health_server()
    bot.remove_webhook()  # webhook qolib ketgan bo'lsa, polling 409 xatosi beradi
    me = bot.get_me()
    try:
        bot.set_my_commands([
            types.BotCommand("start", "Botni boshlash"),
            types.BotCommand("mode", "Javob rejimi"),
            types.BotCommand("clear", "Suhbatni tozalash"),
            types.BotCommand("rasm", "Rasm chizish"),
            types.BotCommand("tahrir", "Rasmni tahrirlash"),
            types.BotCommand("fayl", "Yangi fayl yaratish"),
            types.BotCommand("fayltahrir", "Oxirgi faylni tahrirlash"),
            types.BotCommand("taqdimot", "Taqdimot yaratish"),
            types.BotCommand("slayd", "Taqdimot slaydini tahrirlash"),
            types.BotCommand("help", "Yordam"),
        ])
    except Exception:  # noqa: BLE001
        log.warning("Buyruqlar ro'yxatini o'rnatib bo'lmadi")
    log.info(
        "@%s ishga tushdi | chat: %s | vision: %s | rasm: %s | taqdimot: %s | zaxira Gemini: %s | kirish: %s",
        me.username, config.CHAT_MODEL, config.VISION_MODEL,
        "yoqilgan" if config.IMAGES_ENABLED else "o'chiq",
        "Presenton" if config.PRESENTON_URL else "oddiy",
        "bor" if config.GEMINI_API_KEY else "yo'q",
        f"{len(config.ALLOWED_USERS)} ta ID" if config.ALLOWED_USERS else "hamma uchun ochiq",
    )
    bot.infinity_polling(
        skip_pending=True, timeout=30, long_polling_timeout=30,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
