
"""Foydalanuvchi holatini xotirada saqlash moduli.

Imkoniyatlar:
- Suhbat tarixini saqlash va tozalash.
- Foydalanuvchi rejimini saqlash.
- Oxirgi rasm va taqdimotni saqlash.
- Yuklangan fayl matnini vaqtinchalik saqlash.
- Fayl kontekstini DOC_TTL_MIN vaqtidan keyin avtomatik o'chirish.
- Rasm va fayllar uchun kunlik limitlar.
- So'rovlar orasidagi kutish va foydalanuvchi blokirovkasi.
- Foydalanuvchilar faolligini hisobga olish.
- Xotira hajmini cheklash va ma'lumotlarni xavfsiz nusxalash.

Eslatma:
Bu modul xotiradan foydalanadi. Bot qayta ishga tushsa,
saqlangan ma'lumotlar yo'qoladi.
"""

import copy
import os
import threading
import time

from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone

import config
from keyboards import DEFAULT_MODE


# ============================================================
# UMUMIY SOZLAMALAR
# ============================================================

USERS_CAP = 5000

# Hujjat kontekstining saqlanish vaqti:
# avval config.DOC_TTL_MIN, keyin DOC_TTL_MIN environment,
# ikkalasi ham bo'lmasa 30 daqiqa.
try:
    _DOC_TTL_MIN = int(
        getattr(
            config,
            "DOC_TTL_MIN",
            os.getenv("DOC_TTL_MIN", "30"),
        )
    )
except (TypeError, ValueError):
    _DOC_TTL_MIN = 30

# 0 yoki manfiy qiymat ham xavfsizlik uchun 1 daqiqaga teng.
DOC_TTL_MIN = max(1, min(_DOC_TTL_MIN, 7 * 24 * 60))
DOC_TTL_SECONDS = DOC_TTL_MIN * 60

_lock = threading.RLock()

_history: OrderedDict = OrderedDict()
_modes: OrderedDict = OrderedDict()
_images: OrderedDict = OrderedDict()
_image_prompts: OrderedDict = OrderedDict()
_files: OrderedDict = OrderedDict()
_decks: OrderedDict = OrderedDict()

# Oxirgi ish turi: "image", "file" yoki "deck".
_recent: OrderedDict = OrderedDict()

_last_request: OrderedDict = OrderedDict()
_image_quota: OrderedDict = OrderedDict()
_file_quota: OrderedDict = OrderedDict()
_user_locks: OrderedDict = OrderedDict()

# Botga yozgan foydalanuvchilar.
# Bot qayta ishga tushganda bu ro'yxat ham tozalanadi.
_seen: OrderedDict = OrderedDict()


# ============================================================
# ICHKI YORDAMCHI FUNKSIYALAR
# ============================================================

def _put(store: OrderedDict, key, value, cap: int) -> None:
    """Qiymatni saqlaydi va xotira hajmini belgilangan chegarada tutadi."""
    store[key] = value
    store.move_to_end(key)

    while len(store) > max(1, cap):
        store.popitem(last=False)


def _clear_recent_if(uid: int, kind: str) -> None:
    """Oxirgi ish turi o'chirilgan ma'lumotga tegishli bo'lsa, tozalaydi."""
    if _recent.get(uid) == kind:
        _recent.pop(uid, None)


def _file_expired(file_data: dict, now: float | None = None) -> bool:
    """Faylning TTL muddati tugagan-tugamaganini tekshiradi."""
    if not isinstance(file_data, dict):
        return True

    timestamp = file_data.get("ts")

    # Vaqt belgisi yo'q yoki buzilgan bo'lsa, xavfsiz tomonga o'tamiz:
    # eski fayl kontekstidan foydalanmaymiz.
    if not isinstance(timestamp, (int, float)):
        return True

    if now is None:
        now = time.monotonic()

    age = now - timestamp

    # Kelajakdagi vaqt belgisi ham noto'g'ri ma'lumot deb qaraladi.
    if age < 0:
        return True

    return age >= DOC_TTL_SECONDS


def cleanup_expired() -> int:
    """Muddati tugagan hujjatlarni o'chiradi.

    Qaytaradi:
        O'chirilgan hujjatlar soni.
    """
    now = time.monotonic()
    removed = 0

    with _lock:
        for uid, file_data in list(_files.items()):
            if _file_expired(file_data, now):
                _files.pop(uid, None)
                _clear_recent_if(uid, "file")
                removed += 1

    return removed


# ============================================================
# SUHBAT TARIXI
# ============================================================

def get_history(uid: int) -> list[dict]:
    """Suhbat tarixining nusxasini qaytaradi."""
    with _lock:
        history = _history.get(uid, [])
        return copy.deepcopy(history)


def remember(uid: int, user_text: str, answer: str) -> None:
    """Muvaffaqiyatli savol-javob juftligini tarixga qo'shadi."""
    cut = max(100, getattr(config, "HISTORY_MSG_CHARS", 1500))

    user_text = str(user_text or "")[:cut]
    answer = str(answer or "")[:cut]

    with _lock:
        history = list(_history.get(uid, []))

        history.append({
            "role": "user",
            "content": user_text,
        })
        history.append({
            "role": "assistant",
            "content": answer,
        })

        limit = max(2, getattr(config, "HISTORY_LIMIT", 10))

        # Tarixda faqat to'liq savol-javob juftliklari qolsin.
        limit -= limit % 2
        history = history[-limit:]

        _put(_history, uid, history, USERS_CAP)


def clear_history(uid: int) -> None:
    """Suhbat tarixini va vaqtinchalik media kontekstini tozalaydi."""
    with _lock:
        _history.pop(uid, None)
        _images.pop(uid, None)
        _image_prompts.pop(uid, None)
        _files.pop(uid, None)
        _decks.pop(uid, None)
        _recent.pop(uid, None)


# ============================================================
# REJIM
# ============================================================

def get_mode(uid: int) -> str:
    """Foydalanuvchining javob rejimini qaytaradi."""
    with _lock:
        return _modes.get(uid, DEFAULT_MODE)


def set_mode(uid: int, key: str) -> None:
    """Foydalanuvchining javob rejimini saqlaydi."""
    with _lock:
        _put(_modes, uid, key, USERS_CAP)


# ============================================================
# RASM
# ============================================================

def set_image(uid: int, data: bytes) -> None:
    """Oxirgi rasmni saqlaydi."""
    if not isinstance(data, bytes):
        raise TypeError("Rasm ma'lumoti bytes formatida bo'lishi kerak.")

    with _lock:
        _put(_images, uid, data, config.IMAGE_CACHE)
        _image_prompts.pop(uid, None)
        _put(_recent, uid, "image", USERS_CAP)


def get_image(uid: int) -> bytes | None:
    """Oxirgi rasmni qaytaradi."""
    with _lock:
        return _images.get(uid)


def set_image_prompt(uid: int, prompt: str | None) -> None:
    """Remember the latest generated image request for correction follow-ups."""
    with _lock:
        if prompt and str(prompt).strip():
            _put(_image_prompts, uid, str(prompt).strip()[:1500], USERS_CAP)
        else:
            _image_prompts.pop(uid, None)


def get_image_prompt(uid: int) -> str | None:
    """Return the latest generated image request, if one is available."""
    with _lock:
        return _image_prompts.get(uid)


# ============================================================
# YUKLANGAN FAYLLAR — TTL HIMOYASI
# ============================================================

def set_file(
    uid: int,
    name: str,
    text: str,
    truncated: bool = False,
) -> None:
    """Fayl matnini vaqtinchalik saqlaydi.

    Har safar yangi fayl yuborilganda TTL vaqti yangidan boshlanadi.
    Faylning o'zi emas, ajratib olingan matni saqlanadi.
    """
    if not isinstance(text, str):
        text = str(text or "")

    if not isinstance(name, str) or not name.strip():
        name = "fayl"

    # Config orqali belgilangan matn uzunligi chegarasi.
    max_chars = max(1000, getattr(config, "FILE_MAX_CHARS", 20000))
    text = text[:max_chars]

    file_data = {
        "name": name[:255],
        "text": text,
        "truncated": bool(truncated),
        "ts": time.monotonic(),
    }

    with _lock:
        _put(_files, uid, file_data, config.FILE_CACHE)
        _put(_recent, uid, "file", USERS_CAP)


def get_file(uid: int) -> dict | None:
    """Fayl matnini qaytaradi; muddati tugagan bo'lsa, o'chiradi.

    Muhim:
    Muddati o'tgan fayl uchun None qaytariladi. Shuning uchun
    docs.get(uid) orqali ham eskirgan hujjat AI'ga uzatilmaydi.
    """
    with _lock:
        file_data = _files.get(uid)

        if file_data is None:
            return None

        if _file_expired(file_data):
            _files.pop(uid, None)
            _clear_recent_if(uid, "file")
            return None

        # Faol ishlatilayotgan faylni LRU tartibida yangilaymiz.
        _files.move_to_end(uid)

        return copy.deepcopy(file_data)


def clear_file(uid: int) -> None:
    """Foydalanuvchining yuklangan fayl kontekstini darhol o'chiradi."""
    with _lock:
        _files.pop(uid, None)
        _clear_recent_if(uid, "file")


def file_age_seconds(uid: int) -> float | None:
    """Fayl saqlanganidan beri o'tgan vaqtni soniyalarda qaytaradi."""
    with _lock:
        file_data = _files.get(uid)

        if file_data is None or _file_expired(file_data):
            if file_data is not None:
                _files.pop(uid, None)
                _clear_recent_if(uid, "file")
            return None

        return max(0.0, time.monotonic() - file_data["ts"])


def file_remaining_seconds(uid: int) -> int | None:
    """Fayl konteksti tugashigacha qolgan soniyalarni qaytaradi."""
    age = file_age_seconds(uid)

    if age is None:
        return None

    return max(0, int(DOC_TTL_SECONDS - age))


# ============================================================
# TAQDIMOT
# ============================================================

def set_deck(uid: int, deck: dict, images: dict) -> None:
    """Taqdimot tuzilmasi va unga tegishli rasmlarni saqlaydi."""
    deck_data = {
        "deck": copy.deepcopy(deck),
        "images": copy.deepcopy(images),
        "ts": time.monotonic(),
    }

    with _lock:
        _put(_decks, uid, deck_data, config.DECK_CACHE)
        _put(_recent, uid, "deck", USERS_CAP)


def get_deck(uid: int) -> dict | None:
    """Oxirgi taqdimotning nusxasini qaytaradi."""
    with _lock:
        deck_data = _decks.get(uid)

        if deck_data is None:
            return None

        _decks.move_to_end(uid)
        return copy.deepcopy(deck_data)


def clear_deck(uid: int) -> None:
    """Saqlangan taqdimotni o'chiradi."""
    with _lock:
        _decks.pop(uid, None)
        _clear_recent_if(uid, "deck")


def recent_kind(uid: int) -> str:
    """Oxirgi saqlangan ish turini qaytaradi."""
    with _lock:
        return _recent.get(uid, "")


# ============================================================
# FOYDALANUVCHILAR RO'YXATI
# ============================================================

def track(
    uid: int,
    name: str,
    username: str,
    allowed: bool,
) -> bool:
    """Foydalanuvchi faolligini qayd qiladi.

    Birinchi marta uchragan bo'lsa True, aks holda False qaytaradi.
    """
    now = time.time()

    with _lock:
        record = _seen.get(uid)
        is_new = record is None

        if record is None:
            record = {
                "first": now,
                "count": 0,
            }

        record.update({
            "name": str(name or "")[:200],
            "username": str(username or "")[:100],
            "allowed": bool(allowed),
            "last": now,
        })

        record["count"] += 1
        _put(_seen, uid, record, USERS_CAP)

    return is_new


def list_users(
    limit: int = 30,
) -> tuple[int, list[tuple[int, dict]]]:
    """Jami foydalanuvchilar va eng faol oxirgi foydalanuvchilarni qaytaradi."""
    limit = max(0, int(limit))

    with _lock:
        items = list(_seen.items())
        total = len(items)

        if limit == 0:
            return total, []

        selected = items[-limit:]

        result = [
            (uid, copy.deepcopy(record))
            for uid, record in reversed(selected)
        ]

    return total, result


# ============================================================
# SO'ROVLAR ORASIDAGI KUTISH
# ============================================================

def rate_wait(uid: int) -> float:
    """Ruxsat bo'lsa 0, aks holda qolgan kutish vaqtini qaytaradi."""
    now = time.monotonic()

    with _lock:
        last = _last_request.get(uid)

        if last is not None:
            wait = config.RATE_LIMIT_SECONDS - (now - last)

            if wait > 0:
                return wait

        _put(_last_request, uid, now, USERS_CAP)

    return 0.0


# ============================================================
# KUNLIK LIMITLAR
# ============================================================

def _quota_left(
    store: OrderedDict,
    uid: int,
    limit: int,
) -> int:
    """Bugungi kun uchun qolgan limitni hisoblaydi."""
    if limit <= 0:
        # 0 — cheklanmagan limit.
        return 10**9

    today = datetime.now(timezone.utc).date()

    with _lock:
        day, count = store.get(uid, (today, 0))

        if day != today:
            count = 0

        return max(0, limit - count)


def _quota_count(store: OrderedDict, uid: int) -> None:
    """Bugungi kun uchun ishlatilgan limitni bittaga oshiradi."""
    today = datetime.now(timezone.utc).date()

    with _lock:
        day, count = store.get(uid, (today, 0))

        if day != today:
            count = 0

        _put(store, uid, (today, count + 1), USERS_CAP)


def images_left(uid: int) -> int:
    """Bugun nechta rasm yaratish limiti qolganini qaytaradi."""
    return _quota_left(
        _image_quota,
        uid,
        config.IMAGE_DAILY_LIMIT,
    )


def count_image(uid: int) -> None:
    """Rasm limiti ishlatilganini qayd qiladi."""
    _quota_count(_image_quota, uid)


def files_left(uid: int) -> int:
    """Bugun nechta fayl yaratish limiti qolganini qaytaradi."""
    return _quota_left(
        _file_quota,
        uid,
        config.FILE_DAILY_LIMIT,
    )


def count_file(uid: int) -> None:
    """Fayl limiti ishlatilganini qayd qiladi."""
    _quota_count(_file_quota, uid)


# ============================================================
# FOYDALANUVCHI BLOKIROVKASI
# ============================================================

@contextmanager
def user_slot(uid: int):
    """Bitta foydalanuvchining parallel so'rovlarini cheklaydi.

    Ishlatish:
        with user_slot(uid) as ok:
            if not ok:
                # Oldingi so'rov hali tugamagan.
                return
            # So'rovni bajarish.

    Blokirovka faqat so'rov bajarilayotgan paytda ushlab turiladi.
    """
    with _lock:
        user_lock = _user_locks.get(uid)

        if user_lock is None:
            user_lock = threading.Lock()
            _put(_user_locks, uid, user_lock, USERS_CAP)

        else:
            _user_locks.move_to_end(uid)

    acquired = user_lock.acquire(blocking=False)

    try:
        yield acquired
    finally:
        if acquired:
            user_lock.release()
