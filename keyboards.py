"""Inline tugmalar va javob rejimlari."""
from telebot import types

LANG_RULE = (
    "Foydalanuvchi qaysi tilda yozsa, shu tilda javob ber. Til aniq bo'lmasa, o'zbek tilida javob ber."
)

MODES = {
    "friendly": (
        "😊 Do'stona",
        f"Sen do'stona, iliq va aqlli yordamchisan. Aniq va tushunarli javob ber. {LANG_RULE}",
    ),
    "coder": (
        "👨‍💻 Dasturchi",
        f"Sen tajribali senior dasturchisan. Kod misollari bilan, best practice'larga amal qilib tushuntir. {LANG_RULE}",
    ),
    "teacher": (
        "🎓 O'qituvchi",
        f"Sen sabrli o'qituvchisan. Murakkab mavzularni oddiy misollar bilan, bosqichma-bosqich tushuntir. {LANG_RULE}",
    ),
    "short": (
        "⚡ Qisqa",
        f"Juda qisqa va lo'nda javob ber, ko'pi bilan 3-4 gap. {LANG_RULE}",
    ),
}
DEFAULT_MODE = "friendly"


def main_menu() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🎭 Rejim", callback_data="menu:modes"),
        types.InlineKeyboardButton("🧹 Tozalash", callback_data="menu:clear"),
    )
    kb.add(types.InlineKeyboardButton("ℹ️ Yordam", callback_data="menu:help"))
    return kb


def modes_menu(current: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for key, (title, _) in MODES.items():
        label = f"✅ {title}" if key == current else title
        buttons.append(types.InlineKeyboardButton(label, callback_data=f"mode:{key}"))
    kb.add(*buttons)
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="menu:back"))
    return kb
