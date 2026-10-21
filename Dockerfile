# ================================================================
# 1. Asosiy image
# ================================================================
FROM python:3.12-slim AS base

# Python sozlamalari
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ================================================================
# 2. Tizim paketlari
# ================================================================
# PDF'da o'zbek harflari (oʻ, gʻ, kirill) chiqishi uchun DejaVu shrifti
# lxml, Pillow, reportlab uchun kerakli tizim kutubxonalari
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        fonts-dejavu-extra \
        libxml2 \
        libxslt1.1 \
        libjpeg62-turbo \
        zlib1g \
        libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

# ================================================================
# 3. Ishchi papka
# ================================================================
WORKDIR /app

# ================================================================
# 4. Python kutubxonalari
# ================================================================
# Avval faqat requirements.txt ni nusxalaymiz — Docker keshidan foydalanish uchun
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ================================================================
# 5. Kodni nusxalash
# ================================================================
# .dockerignore orqali keraksiz fayllar chiqarib tashlanadi
COPY . .

# ================================================================
# 6. Root bo'lmagan foydalanuvchi
# ================================================================
RUN useradd --create-home --shell /bin/bash bot \
    && chown -R bot:bot /app
USER bot

# ================================================================
# 7. Port va ishga tushirish
# ================================================================
# Health-check server uchun port (config.py dagi HEALTH_PORT bilan mos)
EXPOSE 8080

# Asosiy fayl bot.py (agar app.py bo'lsa, o'zgartiring)
CMD ["python", "bot.py"]