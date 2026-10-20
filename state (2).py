"""Foydalanuvchi holati (xotirada): tarix, rejim, oxirgi rasm/fayl, limitlar.
Hamma lug'atlar o'lchami cheklangan (eng eskisi o'chadi), shuning uchun xotira cheksiz
o'smaydi."""
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone

import config
from keyboards import DEFAULT_MODE

USERS_CAP = 5000

_lock = threading.Lock()
_history: OrderedDict = OrderedDict()
_modes: OrderedDict = OrderedDict()
_images: OrderedDict = OrderedDict()
_files: OrderedDict = OrderedDict()
_decks: OrderedDict = OrderedDict()
_recent: OrderedDict = OrderedDict()  # oxirgi ish turi: "image", "file" yoki "deck"
_last_request: OrderedDict = OrderedDict()
_image_quota: OrderedDict = OrderedDict()
_file_quota: OrderedDict = OrderedDict()
_user_locks: OrderedDict = OrderedDict()
_seen: OrderedDict = OrderedDict()  # botga yozgan foydalanuvchilar (bot qayta ishga tushsa tozalanadi)


def _put(store: OrderedDict, key, value, cap: int) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > cap:
        store.popitem(last=False)


# ---------------------------------------------------------------- tarix
def get_history(uid: int) -> list[dict]:
    with _lock:
        return list(_history.get(uid, []))


def remember(uid: int, user_text: str, answer: str) -> None:
    """Faqat MUVAFFAQIYATLI almashinuvni saqlaydi (savol + javob juftligi)."""
    cut = config.HISTORY_MSG_CHARS
    with _lock:
        h = list(_history.get(uid, []))
        h.append({"role": "user", "content": user_text[:cut]})
        h.append({"role": "assistant", "content": answer[:cut]})
        _put(_history, uid, h[-config.HISTORY_LIMIT:], USERS_CAP)


def clear_history(uid: int) -> None:
    with _lock:
        _history.pop(uid, None)
        _images.pop(uid, None)
        _files.pop(uid, None)
        _decks.pop(uid, None)
        _recent.pop(uid, None)


# ---------------------------------------------------------------- rejim
def get_mode(uid: int) -> str:
    with _lock:
        return _modes.get(uid, DEFAULT_MODE)


def set_mode(uid: int, key: str) -> None:
    with _lock:
        _put(_modes, uid, key, USERS_CAP)


# ---------------------------------------------------------------- oxirgi rasm / fayl
def set_image(uid: int, data: bytes) -> None:
    with _lock:
        _put(_images, uid, data, config.IMAGE_CACHE)
        _put(_recent, uid, "image", USERS_CAP)


def get_image(uid: int) -> bytes | None:
    with _lock:
        return _images.get(uid)


def set_file(uid: int, name: str, text: str, truncated: bool = False) -> None:
    """Faylning MATNI saqlanadi (baytlari emas) — xotira tejaladi."""
    with _lock:
        _put(_files, uid, {"name": name, "text": text, "truncated": truncated,
                           "ts": time.monotonic()}, config.FILE_CACHE)
        _put(_recent, uid, "file", USERS_CAP)


def get_file(uid: int) -> dict | None:
    with _lock:
        f = _files.get(uid)
        return dict(f) if f else None


def set_deck(uid: int, deck: dict, images: dict) -> None:
    """Taqdimot tuzilmasi + yaratilgan rasmlar (prompt -> bayt) saqlanadi; tahrirda rasmlar qayta chizilmaydi."""
    with _lock:
        _put(_decks, uid, {"deck": deck, "images": images, "ts": time.monotonic()}, config.DECK_CACHE)
        _put(_recent, uid, "deck", USERS_CAP)


def get_deck(uid: int) -> dict | None:
    with _lock:
        d = _decks.get(uid)
        return dict(d) if d else None


def recent_kind(uid: int) -> str:
    with _lock:
        return _recent.get(uid, "")


# ---------------------------------------------------------------- foydalanuvchilar ro'yxati
def track(uid: int, name: str, username: str, allowed: bool) -> bool:
    """Foydalanuvchini qayd qiladi. Birinchi marta ko'rilsa True qaytaradi."""
    now = time.time()
    with _lock:
        rec = _seen.get(uid)
        new = rec is None
        if new:
            rec = {"first": now, "count": 0}
        rec.update(name=name, username=username, allowed=allowed, last=now)
        rec["count"] += 1
        _put(_seen, uid, rec, USERS_CAP)
    return new


def list_users(limit: int = 30) -> tuple[int, list[tuple[int, dict]]]:
    """(jami soni, oxirgi faol foydalanuvchilar — eng yangisi birinchi)."""
    with _lock:
        items = list(_seen.items())
    return len(items), [(u, dict(r)) for u, r in reversed(items[-limit:])]


# ---------------------------------------------------------------- limitlar
def rate_wait(uid: int) -> float:
    """0 qaytarsa — ruxsat (va vaqt belgilanadi); aks holda necha soniya kutish kerakligini qaytaradi."""
    now = time.monotonic()
    with _lock:
        last = _last_request.get(uid)
        if last is not None:
            wait = config.RATE_LIMIT_SECONDS - (now - last)
            if wait > 0:
                return wait
        _put(_last_request, uid, now, USERS_CAP)
    return 0.0


def _quota_left(store: OrderedDict, uid: int, limit: int) -> int:
    if limit <= 0:
        return 10**9
    today = datetime.now(timezone.utc).date()
    with _lock:
        day, count = store.get(uid, (today, 0))
        if day != today:
            count = 0
        return max(0, limit - count)


def _quota_count(store: OrderedDict, uid: int) -> None:
    today = datetime.now(timezone.utc).date()
    with _lock:
        day, count = store.get(uid, (today, 0))
        if day != today:
            count = 0
        _put(store, uid, (today, count + 1), USERS_CAP)


def images_left(uid: int) -> int:
    return _quota_left(_image_quota, uid, config.IMAGE_DAILY_LIMIT)


def count_image(uid: int) -> None:
    _quota_count(_image_quota, uid)


def files_left(uid: int) -> int:
    return _quota_left(_file_quota, uid, config.FILE_DAILY_LIMIT)


def count_file(uid: int) -> None:
    _quota_count(_file_quota, uid)


@contextmanager
def user_slot(uid: int):
    """Bir foydalanuvchining so'rovlari bir vaqtda ishlamasligi uchun (tarix buzilmasin).
    `with user_slot(uid) as ok:` — ok False bo'lsa, oldingi so'rov hali tugamagan."""
    with _lock:
        lk = _user_locks.get(uid)
        if lk is None:
            lk = threading.Lock()
        _put(_user_locks, uid, lk, USERS_CAP)  # lug'at cheksiz o'smasin
    acquired = lk.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lk.release()
