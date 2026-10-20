"""Yagona kirish nuqtasi: Telegram bot + veb-ilova serveri (APK shunga ulanadi).
Veb-ilova faqat APP_PASSWORD berilganda yoqiladi (parolsiz hech qachon ochiq qolmaydi)."""
import logging
import os

import bot
import config

log = logging.getLogger("app")


def main() -> None:
    if os.getenv("APP_PASSWORD", "").strip():
        import web

        web.start(config.HEALTH_PORT or 8080)
        config.HEALTH_PORT = 0  # veb-server health-check vazifasini ham bajaradi
    else:
        log.warning("APP_PASSWORD berilmagan — veb-ilova (APK) o'chiq, faqat Telegram bot ishlaydi")
    bot.main()


if __name__ == "__main__":
    main()
