import os
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
MAX_HISTORY = 10
history = {}

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
        data = r.json()
        return pick_image(data)
    except Exception:
        print("Cloudflare tahrir xato:", r.status_code, r.text[:500])
        raise Exception("Cloudflare tahrir xato berdi")

@bot.message_handler(commands=["start"])
def start(m):
    history.pop(m.chat.id, None)
    bot.reply_to(
        m,
        "Salom! Savol bering yoki rasm yuboring.\n"
        "/rasm tavsif - yangi rasm yaratadi\n"
        "Rasm tagiga /tahrir va buyruq yozsangiz, rasmni o'zgartiradi "
        "(masalan: /tahrir make the sky purple).",
    )

@bot.message_handler(commands=["rasm"])
def draw(m):
    prompt = m.text.partition(" ")[2].strip()
    if not prompt:
        bot.reply_to(m, "Tavsif yozing: /rasm quyosh botayotgan dengiz")
        return
    try:
        bot.send_chat_action(m.chat.id, "upload_photo")
        img = make_image(prompt)
        bot.send_photo(m.chat.id, img, reply_to_message_id=m.message_id)
    except Exception as e:
        print("XATO (rasm yaratish):", e)
        bot.reply_to(m, "Rasm yaratib bo'lmadi, qayta urinib ko'ring.")

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

    if caption.lower().startswith("/tahrir"):
        prompt = caption.partition(" ")[2].strip()
        if not prompt:
            bot.reply_to(m, "Buyruq yozing: /tahrir make the sky purple")
            return
        try:
            bot.send_chat_action(m.chat.id, "upload_photo")
            out = edit_image(img, prompt)
            bot.send_photo(m.chat.id, out, reply_to_message_id=m.message_id)
        except Exception as e:
            print("XATO (tahrir):", e)
            bot.reply_to(m, "Rasmni tahrirlab bo'lmadi, qayta urinib ko'ring.")
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
    try:
        messages = history.get(m.chat.id, []) + [
            {"role": "user", "content": m.text}
        ]
        answer = call_groq(MODEL, messages)
        remember(m.chat.id, m.text, answer)
        bot.reply_to(m, answer)
    except Exception as e:
        print("XATO:", e)
        bot.reply_to(m, "Xatolik, qayta urinib ko'ring.")

print("AI bot ishga tushdi...")
bot.infinity_polling()
