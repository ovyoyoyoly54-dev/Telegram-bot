"""Foydalanuvchi holati (xotirada): tarix, rejim, oxirgi rasm, limitlar.
Hamma lug'atlar o'lchami cheklangan (eng eskisi o'chadi), shuning uchun xotira cheksiz o'smaydi."""
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
_last_request: OrderedDict = OrderedDict()
_image_quota: OrderedDict = OrderedDict()
_user_locks: dict = {}


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


# ---------------------------------------------------------------- rejim

def get_mode(uid: int) -> str:
    with _lock:
        return _modes.get(uid, DEFAULT_MODE)


def set_mode(uid: int, key: str) -> None:
    with _lock:
        _put(_modes, uid, key, USERS_CAP)


# ---------------------------------------------------------------- oxirgi rasm

def set_image(uid: int, data: bytes) -> None:
    with _lock:
        _put(_images, uid, data, config.IMAGE_CACHE)


def get_image(uid: int) -> bytes | None:
    with _lock:
        return _images.get(uid)


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


def images_left(uid: int) -> int:
    limit = config.IMAGE_DAILY_LIMIT
    if limit <= 0:
        return 10**9
    today = datetime.now(timezone.utc).date()
    with _lock:
        day, count = _image_quota.get(uid, (today, 0))
        if day != today:
            count = 0
        return max(0, limit - count)


def count_image(uid: int) -> None:
    today = datetime.now(timezone.utc).date()
    with _lock:
        day, count = _image_quota.get(uid, (today, 0))
        if day != today:
            count = 0
        _put(_image_quota, uid, (today, count + 1), USERS_CAP)


@contextmanager
def user_slot(uid: int):
    """Bir foydalanuvchining so'rovlari bir vaqtda ishlamasligi uchun (tarix buzilmasin).
    `with user_slot(uid) as ok:` — ok False bo'lsa, oldingi so'rov hali tugamagan."""
    with _lock:
        lk = _user_locks.setdefault(uid, threading.Lock())
    acquired = lk.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lk.release()
