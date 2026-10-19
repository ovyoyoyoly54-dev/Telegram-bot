import os
import requests
import telebot

TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"

bot = telebot.TeleBot(TOKEN)

def ask(text):
    r = requests.post(
        URL,
        headers={"Authorization": "Bearer " + GROQ_API_KEY},
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": text}],
        },
        timeout=60,
    )
    data = r.json()
    if "choices" not in data:
        print(r.status_code, data)
        raise Exception("Groq xato berdi")
    return data["choices"][0]["message"]["content"][:4000]

@bot.message_handler(commands=["start"])
def start(m):
    bot.reply_to(m, "Salom! Savol bering.")

@bot.message_handler(func=lambda m: True)
def chat(m):
    try:
        bot.reply_to(m, ask(m.text))
    except Exception as e:
        print("XATO:", e)
        bot.reply_to(m, "Xatolik, qayta urinib ko'ring.")

print("AI bot ishga tushdi...")
bot.infinity_polling()
