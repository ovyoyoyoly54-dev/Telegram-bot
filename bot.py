import os
import requests
import telebot

TOKEN = os.environ["TELEGRAM_TOKEN"]
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]

URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
       "gemini-3.8-flash:generateContent?key=" + GOOGLE_API_KEY)

bot = telebot.TeleBot(TOKEN)

def ask(text):
    r = requests.post(
        URL,
        json={"contents": [{"parts": [{"text": text}]}]},
        timeout=60,
    )
    data = r.json()
    if "candidates" not in data:
        print(r.status_code, data)
        raise Exception("Gemini xato berdi")
    return data["candidates"][0]["content"]["parts"][0]["text"]

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
