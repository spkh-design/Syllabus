"""Защита от спама команд.

1. Кулдаун — повторная команда того же имени от того же пользователя
   в том же чате в течение N секунд игнорируется.
2. Отмена предыдущей — для команд, делающих запросы к API.
"""

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Callable

logger = logging.getLogger(__name__)

# Кулдаун-таймеры: (from_id, peer_id, command) -> time.monotonic()
_cooldowns: dict[tuple[int, int, str], float] = {}

# Активные cancellable-команды: (from_id, peer_id, command) -> generation
_generations: dict[tuple[int, int, str], int] = {}

_CLEANUP_MULTIPLIER = 2


def _cleanup(cooldown_seconds: int) -> None:
    """Удаляет старые записи, чтобы память не росла."""
    threshold = cooldown_seconds * _CLEANUP_MULTIPLIER
    now = time.monotonic()
    to_delete = [k for k, ts in _cooldowns.items() if (now - ts) > threshold]
    for k in to_delete:
        del _cooldowns[k]


# ---------- Кулдаун ----------


def check_cooldown(from_id: int, peer_id: int, command: str, cooldown_seconds: int) -> bool:
    """Проверяет кулдаун. True — можно выполнять, False — рано."""
    if cooldown_seconds <= 0:
        return True

    _cleanup(cooldown_seconds)

    key = (from_id, peer_id, command)
    now = time.monotonic()
    last = _cooldowns.get(key)

    if last is not None:
        if (now - last) < cooldown_seconds:
            logger.info("Команда %s от from_id=%s заблокирована кулдауном", command, from_id)
            return False

    _cooldowns[key] = now
    return True


# ---------- Отмена ----------


def start_cancellable(from_id: int, peer_id: int, command: str) -> int:
    """Регистрирует запуск. Возвращает generation."""
    key = (from_id, peer_id, command)
    new_gen = _generations.get(key, 0) + 1
    _generations[key] = new_gen
    return new_gen


def is_cancelled(from_id: int, peer_id: int, command: str, generation: int) -> bool:
    """True, если команда устарела (была запущена более новая)."""
    key = (from_id, peer_id, command)
    return _generations.get(key) != generation


def finish_cancellable(from_id: int, peer_id: int, command: str, generation: int) -> None:
    """Завершает команду, если её generation всё ещё актуален."""
    key = (from_id, peer_id, command)
    if _generations.get(key) == generation:
        del _generations[key]


# ---------- Декоратор ----------


def guarded(command: str, cooldown: int = 6, cancellable: bool = False) -> Callable:
    """Декоратор для хендлеров: проверяет кулдаун и опционально отмену."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(message, *args, **kwargs):
            from_id = message.from_id
            peer_id = message.peer_id

            if not check_cooldown(from_id, peer_id, command, cooldown):
                return  # Молча игнорируем спам

            generation = None
            if cancellable:
                generation = start_cancellable(from_id, peer_id, command)

            try:
                return await func(message, *args, **kwargs)
            finally:
                if cancellable and generation is not None:
                    finish_cancellable(from_id, peer_id, command, generation)

        return wrapper

    return decorator
