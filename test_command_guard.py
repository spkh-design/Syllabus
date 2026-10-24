"""Тесты для command_guard.py."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

import command_guard
from command_guard import check_cooldown, start_cancellable, is_cancelled, finish_cancellable, guarded


@pytest.fixture(autouse=True)
def clear_state():
    """Очищает глобальные словари перед каждым тестом."""
    command_guard._cooldowns.clear()
    command_guard._generations.clear()
    yield


# ---------- check_cooldown ----------


def test_cooldown_allows_first_call():
    assert check_cooldown(1, 100, "today", 6) is True


def test_cooldown_blocks_second_call():
    assert check_cooldown(1, 100, "today", 6) is True
    assert check_cooldown(1, 100, "today", 6) is False


def test_cooldown_allows_after_time():
    with patch("command_guard.time.monotonic") as mock_time:
        mock_time.return_value = 100.0
        assert check_cooldown(1, 100, "today", 6) is True

        mock_time.return_value = 105.0
        assert check_cooldown(1, 100, "today", 6) is False

        mock_time.return_value = 107.0
        assert check_cooldown(1, 100, "today", 6) is True


def test_cooldown_different_users_dont_interfere():
    assert check_cooldown(1, 100, "today", 6) is True
    assert check_cooldown(2, 100, "today", 6) is True


def test_cooldown_different_chats():
    assert check_cooldown(1, 100, "today", 6) is True
    assert check_cooldown(1, 200, "today", 6) is True


def test_cooldown_different_commands():
    assert check_cooldown(1, 100, "today", 6) is True
    assert check_cooldown(1, 100, "week", 6) is True


# ---------- Cancellable ----------


def test_cancellable_logic():
    gen1 = start_cancellable(1, 100, "today")
    assert is_cancelled(1, 100, "today", gen1) is False

    # Запускаем новую команду
    gen2 = start_cancellable(1, 100, "today")
    assert is_cancelled(1, 100, "today", gen1) is True
    assert is_cancelled(1, 100, "today", gen2) is False

    # Завершаем старую (не должна затереть новую)
    finish_cancellable(1, 100, "today", gen1)
    assert is_cancelled(1, 100, "today", gen2) is False

    # Завершаем новую (удаляет из словаря)
    finish_cancellable(1, 100, "today", gen2)
    # Теперь любой generation актуален, так как записи нет (по умолчанию 0)
    assert is_cancelled(1, 100, "today", 1) is True


# ---------- Декоратор guarded ----------


@pytest.mark.asyncio
async def test_guarded_decorator_calls_func():
    @guarded("test_cmd", cooldown=6, cancellable=False)
    async def mock_handler(message):
        return "ok"

    message = AsyncMock()
    message.from_id = 1
    message.peer_id = 100

    result = await mock_handler(message)
    assert result == "ok"


@pytest.mark.asyncio
async def test_guarded_decorator_blocks_spam():
    call_count = 0

    @guarded("test_cmd", cooldown=6, cancellable=False)
    async def mock_handler(message):
        nonlocal call_count
        call_count += 1
        return "ok"

    message = AsyncMock()
    message.from_id = 1
    message.peer_id = 100

    await mock_handler(message)
    await mock_handler(message)  # Спам

    assert call_count == 1


@pytest.mark.asyncio
async def test_guarded_decorator_cancellable_runs_finally():
    # Проверяем, что finish_cancellable вызывается, даже если функция упала
    @guarded("test_cmd", cooldown=6, cancellable=True)
    async def mock_handler(message):
        raise ValueError("boom")

    message = AsyncMock()
    message.from_id = 1
    message.peer_id = 100

    with pytest.raises(ValueError):
        await mock_handler(message)

    # Словарь _generations должен быть очищен
    assert (1, 100, "test_cmd") not in command_guard._generations
