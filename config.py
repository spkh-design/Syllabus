"""Загрузка конфигурации из .env.

Обязательные переменные:
    VK_TOKEN — токен сообщества/пользователя ВК.

Опциональные:
    DB_PATH                — путь к БД (по умолчанию 'bot.db')
    CHECK_INTERVAL_MINUTES — интервал проверки в минутах (по умолчанию 30)
    ADMIN_USER_IDS         — ID пользователей ВК (админов) через запятую.
                             Пусто = админ-команды недоступны никому.
    ADMIN_PEER_ID          — ID получателя для служебных сообщений
                             (приветствие при старте). Пусто = не отправлять.
                             Может быть личкой (>0) или беседой (<0).

Пример .env:
    VK_TOKEN=vk1.a.xxxxx
    DB_PATH=bot.db
    CHECK_INTERVAL_MINUTES=30
    ADMIN_USER_IDS=462149562,987654321
    ADMIN_PEER_ID=462149562
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

VK_TOKEN: str = os.getenv("VK_TOKEN", "")
DB_PATH: str = os.getenv("DB_PATH", "bot.db")
CHECK_INTERVAL_MINUTES: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))


def _parse_admin_ids(raw: str) -> list[int]:
    """Парсит строку '123,456,789' в список [123, 456, 789].

    Пропускает пустые элементы и нечисловые значения (с предупреждением).
    """
    if not raw:
        return []
    result: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            result.append(int(chunk))
        except ValueError:
            logger.warning("ADMIN_USER_IDS: пропускаю нечисловое значение %r", chunk)
    return result


ADMIN_USER_IDS: list[int] = _parse_admin_ids(os.getenv("ADMIN_USER_IDS", ""))

_raw_peer = os.getenv("ADMIN_PEER_ID", "").strip()
ADMIN_PEER_ID: int | None = None
if _raw_peer:
    try:
        ADMIN_PEER_ID = int(_raw_peer)
    except ValueError:
        logger.warning("ADMIN_PEER_ID: %r не число — приветствие не будет отправлено",
                       _raw_peer)


def is_admin_id(user_id: int) -> bool:
    """True, если user_id входит в список админов.

    Если ADMIN_USER_IDS пуст — возвращает False (никто не админ).
    """
    return user_id in ADMIN_USER_IDS


# ---------- Проверки при старте ----------

if not VK_TOKEN:
    raise RuntimeError(
        "VK_TOKEN не задан. Скопируйте .env.example в .env и заполните."
    )

if not ADMIN_USER_IDS:
    logger.warning(
        "ADMIN_USER_IDS не задан — админ-команды недоступны никому. "
        "Укажите свой VK ID в .env (несколько — через запятую).",
    )