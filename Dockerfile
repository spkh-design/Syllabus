FROM python:3.13-slim

# Отключаем буферизацию вывода Python — иначе логи не видны сразу
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Сначала зависимости — это кэшируется, если requirements.txt не менялся
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY *.py .
COPY README.md .

# Папка для БД — монтируется через volume
RUN mkdir -p /app/data
VOLUME ["/app/data"]
ENV DB_PATH=/app/data/bot.db

# Запуск
CMD ["python", "bot.py"]