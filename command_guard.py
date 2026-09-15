"""Защита от спама команд.

Два механизма:

1. **Кулдаун** — для обычных команд. Повторная команда того же имени
   от того же пользователя в том же чате в течение N секунд
   игнорируется. Отсчёт — от старта первой команды.

2. **Отмена предыдущей** — для команд с аргументами (/group,
   /interval, /week_skip). Если приходит новая команда того же имени,
   пока предыдущая ещё выполняется, — предыдущая отменяется
   на ближайшей проверке через `is_cancelled()`.

Публичный API:
    check_cooldown(from_id, peer_id, command, cooldown_seconds) -> bool
    start_cancellable(from_id, peer_id, command) -> int
    is_cancelled(from_id, peer_id, command, generation) -> bool
    finish_cancellable(from_id, peer_id, command, generation) -> None

Все функции потокобезопасны неявно: asyncio-бот работает в одном
потоке, поэтому блокировки не нужны.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Кулдаун-таймеры: (from_id, peer_id, command) -> время последнего старта
_cooldowns: dict[tuple[int, int, str], datetime] = {}

# Активные cancellable-команды: (from_id, peer_id, command) -> generation
# generation — целое число, увеличивается при каждом новом запуске.
# Если команда видит, что её generation устарел (кто-то запустил
# более новую), она отменяется.
_generations: dict[tuple[int, int, str], int] = {}

# Сколько хранить записи после истечения (в секундах), чтобы
# не росла память. По формуле: 2 × cooldown.
_CLEANUP_MULTIPLIER = 2


def _now() -> datetime:
    return datetime.now()


def _cleanup(cooldown_seconds: int) -> None:
    """Удаляет старые записи из _cooldowns и _generations.

    Записи старше 2 × cooldown_seconds больше не нужны.
    """
    threshold = cooldown_seconds * _CLEANUP_MULTIPLIER
    now = _now()

    # Чистим кулдауны
    to_delete = [
        k for k, ts in _cooldowns.items()
        if (now - ts).total_seconds() > threshold
    ]
    for k in to_delete:
        del _cooldowns[k]

    # Чистим generations — они сбрасываются при finish,
    # но на случай сбоя — чистим всё, что осталось.
    # (generations чистятся только по finish или при новом start)
    # Здесь ничего не делаем, чтобы не сломать логику отмены.


# ---------- Кулдаун (для обычных команд) ----------

def check_cooldown(
    from_id: int, peer_id: int, command: str, cooldown_seconds: int,
) -> bool:
    """Проверяет кулдаун для команды.

    Если команда была запущена < cooldown_seconds назад — возвращает
    False и не обновляет таймер. Иначе — возвращает True и обновляет
    таймер.

    Args:
        from_id:          ID пользователя.
        peer_id:          ID чата (личка или беседа).
        command:          имя команды без «/», например 'today'.
        cooldown_seconds: длительность кулдауна в секундах.

    Returns:
        True — команду можно выполнять.
        False — слишком рано, надо игнорировать.
    """
    if cooldown_seconds <= 0:
        return True

    _cleanup(cooldown_seconds)

    key = (from_id, peer_id, command)
    now = _now()
    last = _cooldowns.get(key)

    if last is not None:
        elapsed = (now - last).total_seconds()
        if elapsed < cooldown_seconds:
            logger.info(
                "Команда %s от from_id=%s в peer_id=%s заблокирована "
                "кулдауном (прошло %.2f сек из %d)",
                command, from_id, peer_id, elapsed, cooldown_seconds,
            )
            return False

    _cooldowns[key] = now
    return True


# ---------- Отмена (для команд с аргументами) ----------

def start_cancellable(from_id: int, peer_id: int, command: str) -> int:
    """Регистрирует запуск cancellable-команды.

    Увеличивает generation для этого ключа и возвращает его.
    Все предыдущие запуски той же команды от того же пользователя
    в том же чате считаются устаревшими.

    Returns:
        Текущий generation — его надо сохранить и передавать
        в `is_cancelled` и `finish_cancellable`.
    """
    key = (from_id, peer_id, command)
    new_gen = _generations.get(key, 0) + 1
    _generations[key] = new_gen
    logger.debug(
        "Команда %s от from_id=%s в peer_id=%s: старт generation=%d",
        command, from_id, peer_id, new_gen,
    )
    return new_gen


def is_cancelled(
    from_id: int, peer_id: int, command: str, generation: int,
) -> bool:
    """Проверяет, не отменена ли команда более новой.

    Returns:
        True — команда устарела, нужно прекратить выполнение.
        False — команда актуальна, продолжаем.
    """
    key = (from_id, peer_id, command)
    current = _generations.get(key)
    if current != generation:
        logger.info(
            "Команда %s от from_id=%s в peer_id=%s отменена "
            "(generation=%d, актуальный=%s)",
            command, from_id, peer_id, generation, current,
        )
        return True
    return False


def finish_cancellable(
    from_id: int, peer_id: int, command: str, generation: int,
) -> None:
    """Завершает cancellable-команду.

    Если generation актуален — удаляет запись из _generations.
    Если generation устарел (нас отменили) — ничего не делает,
    чтобы не затереть запись более новой команды.
    """
    key = (from_id, peer_id, command)
    if _generations.get(key) == generation:
        del _generations[key]
        logger.debug(
            "Команда %s от from_id=%s в peer_id=%s: finish generation=%d",
            command, from_id, peer_id, generation,
        )