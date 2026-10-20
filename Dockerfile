FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# PDF'da o'zbek harflari (oʻ, gʻ, kirill) chiqishi uchun DejaVu shrifti
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# root bo'lmagan foydalanuvchi bilan ishlatamiz
RUN useradd --create-home bot && chown -R bot:bot /app
USER bot

# Health-check uchun (ixtiyoriy). Bot o'zi Telegram'ga long-polling bilan ulanadi.
EXPOSE 8080

CMD ["python", "app.py"]
