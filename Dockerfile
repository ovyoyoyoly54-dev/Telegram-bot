FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# root bo'lmagan foydalanuvchi bilan ishlatamiz
RUN useradd --create-home bot && chown -R bot:bot /app
USER bot

# Health-check uchun (ixtiyoriy). Bot o'zi Telegram'ga long-polling bilan ulanadi.
EXPOSE 8080

CMD ["python", "bot.py"]
