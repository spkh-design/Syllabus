"""Загрузка конфигурации из .env."""

from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Загружаем .env в os.environ
load_dotenv()

# Установка TZ (для Linux/Docker). На Windows — no-op.
_tz = os.getenv("TZ", "")
if _tz:
    os.environ["TZ"] = _tz
    if hasattr(time, "tzset"):
        time.tzset()


# ---------- Основные настройки ----------

VK_TOKEN: str = os.getenv("VK_TOKEN", "")
DB_PATH: str = os.getenv("DB_PATH", "bot.db")
CHECK_INTERVAL_MINUTES: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))
COMMAND_COOLDOWN_SECONDS: int = int(
    os.getenv("COMMAND_COOLDOWN_SECONDS", "6")
)


# ---------- Админы ----------

def _parse_admin_ids(raw: str) -> list[int]:
    ...


ADMIN_USER_IDS: list[int] = _parse_admin_ids(os.getenv("ADMIN_USER_IDS", ""))

_raw_peer = os.getenv("ADMIN_PEER_ID", "").strip()
ADMIN_PEER_ID: int | None = None
if _raw_peer:
    try:
        ADMIN_PEER_ID = int(_raw_peer)
    except ValueError:
        logger.warning(
            "ADMIN_PEER_ID: %r не число — приветствие не будет отправлено",
            _raw_peer,
        )


def is_admin_id(user_id: int) -> bool:
    ...


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

if COMMAND_COOLDOWN_SECONDS < 0:
    logger.warning(
        "COMMAND_COOLDOWN_SECONDS=%d — отрицательный, будет 0",
        COMMAND_COOLDOWN_SECONDS,
    )
    COMMAND_COOLDOWN_SECONDS = 0