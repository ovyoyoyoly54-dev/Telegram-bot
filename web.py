"""Veb-ilova serveri (APK ichidagi WebView shu sahifani ochadi).
Bot bilan bir jarayonda ishlaydi va bir xil modullardan (llm, docs, images, state) foydalanadi.
Faqat APP_PASSWORD berilganda yoqiladi."""
import hashlib
import hmac
import logging
import mimetypes
import os
import secrets
import threading
import time
import unicodedata
from collections import OrderedDict
from urllib.parse import quote

from flask import Flask, Response, jsonify, request, send_from_directory

import bot as tg  # tasniflash regexlari bilan bir xil mantiq
import config
import docs
import images
import llm
import state
from keyboards import MODES

log = logging.getLogger("web")

APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()
APP_UID = 10**12 + 7                      # ilova foydalanuvchisi (Telegram ID'lari bilan to'qnashmaydi)
TOKEN_DAYS = 90
_SECRET = hashlib.sha256(f"app-v1|{APP_PASSWORD}|{config.TELEGRAM_TOKEN}".encode()).digest()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _asset_dir(name: str) -> str:
    """index.html va icon.png ildizda (yoki static/ papkada) bo'lishi mumkin."""
    for d in (BASE_DIR, os.path.join(BASE_DIR, "static")):
        if os.path.exists(os.path.join(d, name)):
            return d
    return BASE_DIR

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = docs.MAX_FILE_BYTES + 1024 * 1024


class ApiError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- kirish (parol → imzolangan token)

def _sign(payload: str) -> str:
    return hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32]


def make_token() -> str:
    payload = f"{APP_UID}.{int(time.time()) + TOKEN_DAYS * 86400}"
    return f"{payload}.{_sign(payload)}"


def check_token(token: str) -> bool:
    try:
        uid, exp, sig = token.split(".")
        return hmac.compare_digest(sig, _sign(f"{uid}.{exp}")) and int(exp) > time.time()
    except (ValueError, AttributeError):
        return False


_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def _client_ip() -> str:
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "?")


def _locked(ip: str) -> bool:
    now = time.time()
    with _fails_lock:
        recent = [t for t in _fails.get(ip, []) if now - t < 600]
        _fails[ip] = recent
        return len(recent) >= 8


def _fail(ip: str) -> None:
    with _fails_lock:
        _fails.setdefault(ip, []).append(time.time())


def authed(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not (auth.startswith("Bearer ") and check_token(auth[7:])):
            return jsonify(error="Kirish talab qilinadi"), 401
        try:
            return fn(*args, **kwargs)
        except ApiError as exc:
            return jsonify(error=str(exc)), exc.status
        except (llm.LLMError, images.ImageError, docs.DocError) as exc:
            log.warning("Ilova xatosi: %s", exc)
            return jsonify(error=str(exc)), 422
        except Exception:  # noqa: BLE001
            log.exception("Ilovada kutilmagan xato")
            return jsonify(error="Kutilmagan xatolik yuz berdi. Keyinroq urinib ko'ring."), 500

    return wrapper


# ---------------------------------------------------------------- yaratilgan fayllarni vaqtincha saqlash

_files: OrderedDict = OrderedDict()
_files_lock = threading.Lock()
FILE_TTL = 3600
FILE_CAP = 40


def put_file(name: str, data: bytes, mime: str | None = None) -> str:
    """Faylni 1 soatga saqlaydi va taxmin qilib bo'lmaydigan havola qaytaradi (yuklab olish uchun sarlavha shart emas)."""
    fid = secrets.token_urlsafe(16)
    mime = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
    with _files_lock:
        _files[fid] = (name, mime, data, time.time())
        while len(_files) > FILE_CAP:
            _files.popitem(last=False)
    return f"/f/{fid}/{quote(name)}"


@app.get("/f/<fid>/<path:_name>")
def get_file(fid, _name):
    with _files_lock:
        item = _files.get(fid)
        if item and time.time() - item[3] > FILE_TTL:
            del _files[fid]
            item = None
    if not item:
        return jsonify(error="Havola eskirgan, faylni qayta so'rang."), 404
    name, mime, data, _ = item
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode() or "fayl"
    inline = mime.startswith("image/")
    disp = (f"{'inline' if inline else 'attachment'}; filename=\"{ascii_name}\"; "
            f"filename*=UTF-8''{quote(name)}")
    return Response(data, mimetype=mime, headers={"Content-Disposition": disp, "Cache-Control": "private, max-age=3600"})


# ---------------------------------------------------------------- mantiq (bot.py dagi oqim, Telegram'siz)

def _img_name(data: bytes) -> tuple[str, str]:
    return ("rasm.png", "image/png") if data[:4] == b"\x89PNG" else ("rasm.jpg", "image/jpeg")


def _chat(uid: int, text: str, image: bytes | None = None, label: str | None = None) -> dict:
    system = MODES[state.get_mode(uid)][1]
    doc = docs.get(uid)
    if doc:
        system += docs.context_block(doc)
    answer = llm.chat(state.get_history(uid), text, system, image)
    state.remember(uid, label or text, answer)
    return {"kind": "text", "text": answer}


def _check_quota(uid: int) -> None:
    if not config.IMAGES_ENABLED:
        raise ApiError("Rasm funksiyasi hozircha sozlanmagan.")
    if state.images_left(uid) <= 0:
        raise ApiError(f"Bugungi rasm limiti ({config.IMAGE_DAILY_LIMIT} ta) tugadi. Ertaga urinib ko'ring.")


def _deliver_image(uid: int, img: bytes, note: str) -> dict:
    state.count_image(uid)
    try:
        state.set_image(uid, images.shrink(img))
    except Exception:  # noqa: BLE001
        log.warning("Natija rasmini kichraytirib bo'lmadi")
    state.remember(uid, note, "[Rasm yuborildi]")
    name, mime = _img_name(img)
    return {"kind": "image", "url": put_file(name, img, mime), "name": name}


def _draw(uid: int, prompt: str, note: str) -> dict:
    _check_quota(uid)
    return _deliver_image(uid, images.make_image(prompt), note)


def _edit(uid: int, prompt: str, note: str) -> dict:
    img = state.get_image(uid)
    if img is None:
        raise ApiError("Avval tahrirlash uchun rasm yuboring.")
    _check_quota(uid)
    return _deliver_image(uid, images.edit_image(img, prompt), note)


def _file(uid: int, instruction: str) -> dict:
    result = docs.generate(state.get_history(uid), instruction, docs.get(uid))
    docs.store(uid, result.name, result.text)
    state.remember(uid, instruction, f"[Fayl yuborildi: {result.name}] {result.summary}")
    return {"kind": "file", "name": result.name, "url": put_file(result.name, result.data), "text": result.summary}


def _route(uid: int, text: str) -> dict:
    last = state.get_image(uid) if config.IMAGES_ENABLED else None
    doc = docs.get(uid)
    t = tg._norm(text)
    want_image = config.IMAGES_ENABLED and tg.looks_like_image_request(text, last is not None)
    want_file = bool(tg._FILE_HINT.search(t) or (doc and tg._DOCEDIT_HINT.search(t)))
    action, prompt = "chat", ""
    if want_image or want_file:
        try:
            action, prompt = llm.classify(text, last is not None, doc is not None)
        except Exception as exc:  # noqa: BLE001
            log.warning("Tasniflash xatosi: %s", exc)
        if action in ("draw", "edit") and not config.IMAGES_ENABLED:
            action = "chat"
    if action == "draw":
        return _draw(uid, prompt, text)
    if action == "edit":
        return _edit(uid, prompt, text)
    if action == "file":
        return _file(uid, text)
    return _chat(uid, text)


def _handle_upload(uid: int, name: str, mime: str, raw: bytes, caption: str) -> dict:
    notice = ""
    if mime.startswith("image/"):
        try:
            img = images.shrink(raw)
        except Exception:  # noqa: BLE001
            raise ApiError("Rasmni o'qib bo'lmadi. JPG yoki PNG formatda yuboring.")
        state.set_image(uid, img)
        action, prompt = "chat", ""
        if caption and config.IMAGES_ENABLED:
            try:
                action, prompt = llm.classify(caption, True)
            except Exception as exc:  # noqa: BLE001
                log.warning("Tasniflash xatosi: %s", exc)
        if action == "edit":
            return _edit(uid, prompt, caption)
        if action == "draw":
            return _draw(uid, prompt, caption)
        question = caption or "Bu rasmda nima bor? Batafsil tushuntirib ber."
        return _chat(uid, question, image=img, label="[Rasm yuborildi] " + question)

    docs.precheck(name, mime)
    text, truncated = docs.extract(name, raw, mime)
    docs.store(uid, name, text, truncated)
    if truncated:
        notice = f"«{name}» uzun, faqat dastlabki ~{docs.MAX_TEXT_CHARS} belgisi o'qildi."
    if not caption:
        res = _chat(uid, f"Men «{name}» faylini yukladim. Uning mazmunini 3-5 gapda qisqacha tushuntir va nimalar "
                         "qila olishingni (savol-javob, tahrirlash, tarjima, xulosa, boshqa formatga o'tkazish) "
                         "bir gapda ayt.", label=f"[Fayl yuklandi] {name}")
    else:
        action = "chat"
        try:
            action, _ = llm.classify(caption, False, True)
        except Exception as exc:  # noqa: BLE001
            log.warning("Tasniflash xatosi: %s", exc)
        res = _file(uid, caption) if action == "file" else _chat(uid, caption, label=f"[Fayl: {name}] {caption}")
    if notice:
        res["notice"] = notice
    return res


def _slot_or_busy():
    return state.user_slot(APP_UID)


# ---------------------------------------------------------------- API

@app.post("/api/login")
def api_login():
    ip = _client_ip()
    if _locked(ip):
        return jsonify(error="Juda ko'p urinish. 10 daqiqadan keyin qayta urinib ko'ring."), 429
    password = str((request.get_json(silent=True) or {}).get("password", ""))
    if APP_PASSWORD and hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
        return jsonify(token=make_token())
    _fail(ip)
    return jsonify(error="Parol noto'g'ri"), 401


@app.get("/api/state")
@authed
def api_state():
    doc = docs.get(APP_UID)
    return jsonify(
        mode=state.get_mode(APP_UID),
        modes=[{"key": k, "title": v[0]} for k, v in MODES.items()],
        images=config.IMAGES_ENABLED,
        doc=doc["name"] if doc else None,
    )


@app.post("/api/mode")
@authed
def api_mode():
    key = str((request.get_json(silent=True) or {}).get("mode", ""))
    if key not in MODES:
        raise ApiError("Noma'lum rejim", 400)
    state.set_mode(APP_UID, key)
    return jsonify(ok=True)


@app.post("/api/clear")
@authed
def api_clear():
    tg.clear_all(APP_UID)
    return jsonify(ok=True)


@app.post("/api/chat")
@authed
def api_chat():
    text = str((request.get_json(silent=True) or {}).get("text", "")).strip()
    if not text:
        raise ApiError("Xabar bo'sh", 400)
    with _slot_or_busy() as ok:
        if not ok:
            raise ApiError("Oldingi so'rov hali bajarilmoqda, biroz kuting.", 429)
        return jsonify(_route(APP_UID, text[:8000]))


@app.post("/api/upload")
@authed
def api_upload():
    f = request.files.get("file")
    if f is None:
        raise ApiError("Fayl tanlanmadi", 400)
    raw = f.read()
    if len(raw) > docs.MAX_FILE_BYTES:
        raise ApiError(f"Fayl juda katta (ko'pi bilan {docs.MAX_FILE_BYTES // (1024 * 1024)} MB).", 413)
    caption = (request.form.get("text") or "").strip()[:4000]
    with _slot_or_busy() as ok:
        if not ok:
            raise ApiError("Oldingi so'rov hali bajarilmoqda, biroz kuting.", 429)
        return jsonify(_handle_upload(APP_UID, f.filename or "fayl", f.mimetype or "", raw, caption))


# ---------------------------------------------------------------- sahifalar

@app.get("/")
def index():
    resp = send_from_directory(_asset_dir("index.html"), "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/icon.png")
def icon():
    return send_from_directory(_asset_dir("icon.png"), "icon.png")


def start(port: int) -> None:
    """Veb-serverni alohida oqimda ishga tushiradi."""
    def run():
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=port, threads=8, ident="ai")
        except ImportError:
            app.run(host="0.0.0.0", port=port, threaded=True)

    threading.Thread(target=run, daemon=True, name="web").start()
    log.info("Veb-ilova serveri: port %s", port)
