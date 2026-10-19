FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# DejaVu shrifti: PDF fayllarda o'zbekcha/kirillcha harflar to'g'ri chiqishi uchun
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd -m bot && chown -R bot /app
USER bot

CMD ["python", "bot.py"]

