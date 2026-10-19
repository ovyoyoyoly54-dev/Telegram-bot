import os
import base64
import requests
import telebot

TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
VISION_MODEL = "qwen/qwen3.8-27b"
URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"


bot = telebot.TeleBot(TOKEN)

def call_groq(model, content):
    r = requests.post(
        URL,
        headers={"Authorization": "Bearer " + GROQ_API_KEY},
        json={"model": model, "messages": [{"role": "user", "content": content}]},
        timeout=60,
    )
    data = r.json()
    if "choices" not in data:
        print(r.status_code, data)
        raise Exception("Groq xato berdi")
    return data["choices"][0]["message"]["content"][:4000]

@bot.message_handler(commands=["start"])
def start(m):
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
        bot.reply_to(m, call_groq(VISION_MODEL, content))
    except Exception as e:
        print("XATO:", e)
        bot.reply_to(m, "Rasmni o'qib bo'lmadi, qayta urinib ko'ring.")

@bot.message_handler(func=lambda m: True)
def chat(m):
    try:
        bot.reply_to(m, call_groq(MODEL, m.text))
    except Exception as e:
        print("XATO:", e)
        bot.reply_to(m, "Xatolik, qayta urinib ko'ring.")

print("AI bot ishga tushdi...")
bot.infinity_polling()
