"""Определение группы, к которой относится входящая команда.

Правила:
  - Команда в беседе (peer_id >= 2000000000 или < 0) — группа = текущая беседа.
  - Команда в ЛС (peer_id > 0) — используем:
      1) явный аргумент `peer=N` из текста команды, ИЛИ
      2) сохранённую сессию `/use <peer_id>`.

Также модуль умеет:
  - определять, пришло ли сообщение из беседы или из ЛС;
  - парсить `peer=N` из строки аргументов.

Публичный API:
    is_group_chat(peer_id)                -> bool
    parse_peer_arg(args)                  -> (peer_id | None, cleaned_args)
    resolve_peer_id(message, conn, ...)   -> peer_id | None
"""

from __future__ import annotations

import logging
import re

import database as db

logger = logging.getLogger(__name__)

# В VK:
#   ЛС:            peer_id > 0 (обычно до 2_000_000_000)
#   Беседа:        peer_id >= 2_000_000_000
#   Старые группы: peer_id < 0
GROUP_PEER_MIN = 2_000_000_000

# Регулярка для `peer=N` в аргументах. Ловит `peer=123`, `peer = 123`, `peer:123`.
_PEER_RE = re.compile(r"\bpeer\s*[=:]\s*(\d+)", re.IGNORECASE)


def is_group_chat(peer_id: int) -> bool:
    """True, если peer_id — беседа (не ЛС)."""
    return peer_id >= GROUP_PEER_MIN or peer_id < 0


def is_personal_chat(peer_id: int) -> bool:
    """True, если peer_id — личные сообщения."""
    return 0 < peer_id < GROUP_PEER_MIN


def parse_peer_arg(args: str) -> tuple[int | None, str]:
    """Извлекает `peer=N` из строки аргументов.

    Примеры:
        "СП-X yyy peer=2000000000"  -> (2000000000, "СП-X yyy")
        "peer=2000000000"           -> (2000000000, "")
        "123456"                    -> (None, "123456")
        "СП-X yyy"                  -> (None, "СП-X yyy")

    Возвращает:
        (peer_id или None, очищенные аргументы без `peer=N`).
    """
    if not args:
        return None, ""

    match = _PEER_RE.search(args)
    if not match:
        return None, args.strip()

    peer_id = int(match.group(1))
    cleaned = _PEER_RE.sub("", args).strip()
    # Схлопываем двойные пробелы, если они появились после удаления
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return peer_id, cleaned


def resolve_peer_id(message, conn, *, explicit_peer: int | None = None) -> int | None:
    """Определяет peer_id беседы, к которой относится команда.

    Порядок приоритета:
      1. explicit_peer (передан из parse_peer_arg) — если задан явно.
      2. Если команда в беседе — message.peer_id.
      3. Если в ЛС — сохранённая сессия /use (user_sessions).

    Returns:
        peer_id беседы или None, если определить не удалось.
    """
    if explicit_peer is not None:
        return explicit_peer

    if is_group_chat(message.peer_id):
        return message.peer_id

    # ЛС: смотрим, какую группу выбрал пользователь через /use
    session_peer = db.get_user_session(conn, message.from_id)
    if session_peer is not None:
        return session_peer

    return None
