"""Загрузка конфигурации из .env.

Обязательные переменные:
    VK_TOKEN — токен сообщества/пользователя ВК.

Опциональные:
    DB_PATH              — путь к БД (по умолчанию 'bot.db')
    CHECK_INTERVAL_MINUTES — интервал проверки (по умолчанию 30)
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

VK_TOKEN: str = os.getenv("VK_TOKEN", "")
DB_PATH: str = os.getenv("DB_PATH", "bot.db")
CHECK_INTERVAL_MINUTES: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))

if not VK_TOKEN:
    raise RuntimeError(
        "VK_TOKEN не задан. Скопируйте .env.example в .env и заполните."
    )