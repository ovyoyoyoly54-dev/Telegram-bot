import os
import json
import base64
import requests
import telebot

TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")

URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"
VISION_MODEL = "qwen/qwen3.8-27b"
IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
EDIT_MODEL = "@cf/black-forest-labs/flux-2-klein-4b"

SYSTEM = {
    "role": "system",
    "content": "Sen foydali yordamchisan. Foydalanuvchi qaysi tilda yozsa, "
               "shu tilda javob ber, asosan o'zbek tilida.",
}
CLASSIFIER = (
    "Sen xabarni tasniflaysan. Faqat JSON qaytar: "
    '{"action": "chat" | "draw" | "edit", "prompt": "..."}. '
    "draw: foydalanuvchi tavsif asosida YANGI rasm chizishni/yaratishni so'raydi. "
    "edit: foydalanuvchi MAVJUD rasmni o'zgartirishni so'raydi "
    "(fon, rang, ob'ekt qo'shish/olib tashlash, uslub, qora-oq va hokazo); "
    "faqat has_image true bo'lsa edit tanla. "
    "chat: qolgan hamma narsa, shu jumladan rasm haqidagi savollar "
    "('bu nima?', 'tushuntir'). "
    "prompt: rasm modeli uchun qisqa INGLIZCHA ko'rsatma (draw uchun natijaning "
    "tavsifi, edit uchun qilinadigan o'zgarish). chat bo'lsa bo'sh qoldir."
)
KEYWORDS = [
    "rasm", "surat", "chiz", "tasvir", "fon", "rang", "zgartir", "qora",
    "tashla", "image", "picture", "photo", "draw", "edit", "paint",
]
MAX_HISTORY = 10
history = {}
last_image = {}

bot = telebot.TeleBot(TOKEN)

def cf_url(model):
    return ("https://api.cloudflare.com/client/v4/accounts/"
            + CF_ACCOUNT_ID + "/ai/run/" + model)

def call_groq(model, messages):
    r = requests.post(
        URL,
        headers={"Authorization": "Bearer " + GROQ_API_KEY},
        json={"model": model, "messages": [SYSTEM] + messages},
        timeout=90,
    )
    data = r.json()
    if "choices" not in data:
        print(r.status_code, data)
        raise Exception("Groq xato berdi")
    text = data["choices"][0]["message"].get("content") or ""
    if not text.strip():
        print("Bo'sh javob:", data)
        raise Exception("Bo'sh javob")
    return text[:4000]

def classify(text, has_image):
    r = requests.post(
        URL,
        headers={"Authorization": "Bearer " + GROQ_API_KEY},
        json={
            "model": MODEL,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": CLASSIFIER},
                {"role": "user",
                 "content": "has_image: " + str(has_image).lower()
                            + "\nxabar: " + text},
            ],
        },
        timeout=60,
    )
    content = r.json()["choices"][0]["message"].get("content") or ""
    start, end = content.find("{"), content.rfind("}")
    data = json.loads(content[start:end + 1])
    action = data.get("action", "chat")
    prompt = (data.get("prompt") or "").strip()
    if action in ("draw", "edit") and not prompt:
        action = "chat"
    if action == "edit" and not has_image:
        action = "chat"
    return action, prompt

def remember(chat_id, user_text, answer):
    h = history.setdefault(chat_id, [])
    h.append({"role": "user", "content": user_text})
    h.append({"role": "assistant", "content": answer})
    del h[:-MAX_HISTORY]

def pick_image(data):
    result = data.get("result") or {}
    b64 = result.get("image") or data.get("image")
    if not b64:
        raise KeyError("image topilmadi")
    return base64.b64decode(b64)

def make_image(prompt):
    r = requests.post(
        cf_url(IMAGE_MODEL),
        headers={"Authorization": "Bearer " + CF_API_TOKEN},
        json={"prompt": prompt},
        timeout=120,
    )
    data = r.json()
    try:
        return pick_image(data)
    except Exception:
        print("Cloudflare xato:", r.status_code, data)
        raise Exception("Cloudflare xato berdi")

def edit_image(img_bytes, prompt):
    r = requests.post(
        cf_url(EDIT_MODEL),
        headers={"Authorization": "Bearer " + CF_API_TOKEN},
        files={
            "prompt": (None, prompt),
            "input_image_0": ("image.jpg", img_bytes, "image/jpeg"),
        },
        timeout=180,
    )
    try:
        return pick_image(r.json())
    except Exception:
        print("Cloudflare tahrir xato:", r.status_code, r.text[:500])
        raise Exception("Cloudflare tahrir xato berdi")

def send_result(m, img, note):
    last_image[m.chat.id] = img
    remember(m.chat.id, note, "[Rasm yuborildi]")
    bot.send_photo(m.chat.id, img, reply_to_message_id=m.message_id)

def do_draw(m, prompt):
    try:
        bot.send_chat_action(m.chat.id, "upload_photo")
        send_result(m, make_image(prompt), m.text or prompt)
    except Exception as e:
        print("XATO (rasm yaratish):", e)
        bot.reply_to(m, "Rasm yaratib bo'lmadi, qayta urinib ko'ring.")

def do_edit(m, img, prompt):
    try:
        bot.send_chat_action(m.chat.id, "upload_photo")
        send_result(m, edit_image(img, prompt), m.caption or m.text or prompt)
    except Exception as e:
        print("XATO (tahrir):", e)
        bot.reply_to(m, "Rasmni tahrirlab bo'lmadi, qayta urinib ko'ring.")

@bot.message_handler(commands=["start"])
def start(m):
    history.pop(m.chat.id, None)
    bot.reply_to(
        m,
        "Salom! Savol bering yoki rasm yuboring.\n"
        "Rasm chizdirish uchun oddiy yozing: 'dengiz ustida quyosh botayotgan "
        "rasm chiz'.\n"
        "Rasmni o'zgartirish uchun surat yuborib, tagiga nima qilishni yozing "
        "(masalan: 'fonni tun qil').",
    )

@bot.message_handler(commands=["rasm"])
def draw_cmd(m):
    prompt = m.text.partition(" ")[2].strip()
    if not prompt:
        bot.reply_to(m, "Tavsif yozing: /rasm quyosh botayotgan dengiz")
        return
    do_draw(m, prompt)

@bot.message_handler(content_types=["photo"])
def photo(m):
    caption = (m.caption or "").strip()
    try:
        info = bot.get_file(m.photo[-1].file_id)
        img = bot.download_file(info.file_path)
    except Exception as e:
        print("XATO (yuklash):", e)
        bot.reply_to(m, "Rasmni yuklab bo'lmadi, qayta urinib ko'ring.")
        return
    last_image[m.chat.id] = img

    if caption:
        if caption.lower().startswith("/tahrir"):
            action, prompt = "edit", caption.partition(" ")[2].strip()
        else:
            try:
                action, prompt = classify(caption, True)
            except Exception as e:
                print("Tasnif xato:", e)
                action, prompt = "chat", ""
        if action == "edit" and prompt:
            do_edit(m, img, prompt)
            return
        if action == "draw" and prompt:
            do_draw(m, prompt)
            return

    try:
        b64 = base64.b64encode(img).decode()
        savol = caption or "Bu rasmda nima bor? O'zbek tilida javob ber."
        content = [
            {"type": "text", "text": savol},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + b64}},
        ]
        messages = history.get(m.chat.id, []) + [
            {"role": "user", "content": content}
        ]
        answer = call_groq(VISION_MODEL, messages)
        remember(m.chat.id, "[Rasm yuborildi] " + savol, answer)
        bot.reply_to(m, answer)
    except Exception as e:
        print("XATO (rasm):", e)
        bot.reply_to(m, "Rasmni o'qib bo'lmadi, qayta urinib ko'ring.")

@bot.message_handler(func=lambda m: True)
def chat(m):
    text = m.text or ""
    if any(k in text.lower() for k in KEYWORDS):
        try:
            action, prompt = classify(text, m.chat.id in last_image)
        except Exception as e:
            print("Tasnif xato:", e)
            action, prompt = "chat", ""
        if action == "draw":
            do_draw(m, prompt)
            return
        if action == "edit":
            do_edit(m, last_image[m.chat.id], prompt)
            return
    try:
        messages = history.get(m.chat.id, []) + [
            {"role": "user", "content": text}
        ]
        answer = call_groq(MODEL, messages)
        remember(m.chat.id, text, answer)
        bot.reply_to(m, answer)
    except Exception as e:
        print("XATO:", e)
        bot.reply_to(m, "Xatolik, qayta urinib ko'ring.")

print("AI bot ishga tushdi...")
bot.infinity_polling()
