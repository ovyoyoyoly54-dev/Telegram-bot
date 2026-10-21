FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core fonts-dejavu-extra \
        libxml2 libxslt1.1 libjpeg62-turbo zlib1g libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --shell /bin/bash bot && chown -R bot:bot /app
USER bot

EXPOSE 8080

CMD ["python", "bot.py"]