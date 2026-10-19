import os
import base64
import requests
import telebot

TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"
VISION_MODEL = "qwen/qwen3.8-27b"

SYSTEM = {
    "role": "system",
    "content": "Sen foydali yordamchisan. Foydalanuvchi qaysi tilda yozsa, "
               "shu tilda javob ber, asosan o'zbek tilida.",
}
MAX_HISTORY = 10
history = {}

bot = telebot.TeleBot(TOKEN)

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

@bot.message_handler(commands=["start"])
def start(m):
    history.pop(m.chat.id, None)
    bot.reply_to(m, "Salom! Savol bering yoki rasm yuboring.")

@bot.message_handler(content_types=["photo"])
def photo(m):
    try:
        info = bot.get_file(m.photo[-1].file_id)
        img = bot.download_file(info.file_path)
        b64 = base64.b64encode(img).decode()
        savol = m.caption or "Bu rasmda nima bor? O'zbek tilida javob ber."
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
