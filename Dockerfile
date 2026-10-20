FROM python:3.13-slim

# Отключаем буферизацию вывода Python — иначе логи не видны сразу
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Системные зависимости для OCR (Tesseract + русский язык)
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

# Сначала зависимости — это кэшируется, если requirements.txt не менялся
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY *.py ./
COPY README.md ./
COPY holidays.json ./
COPY assets/ ./assets/

# Папка для БД — монтируется через volume
RUN mkdir -p /app/data
VOLUME ["/app/data"]
ENV DB_PATH=/app/data/bot.db

# Запуск
CMD ["python", "bot.py"]