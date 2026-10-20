"""Тесты для bot.py — только приветствие при старте."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import database as db


@pytest.fixture
def bot_module(tmp_path):
    """Изолированный bot.py на временной БД."""
    import bot

    original_conn = bot.conn
    bot.conn = db.init_db(tmp_path / "bot.db")
    yield bot
    bot.conn.close()
    bot.conn = original_conn


@pytest.mark.asyncio
async def test_greeting_skipped_if_db_configured(bot_module):
    """Если группа уже зарегистрирована — приветствие не уходит."""
    db.add_group(bot_module.conn, peer_id=2000000002, group_uuid="grp-419", group_name="419", division="СП-4")
    mock_send = AsyncMock()

    await bot_module.send_startup_greeting(bot_module.conn, admin_peer_id=462149562, send_func=mock_send)
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_greeting_skipped_if_no_admin_peer(bot_module):
    """Если ADMIN_PEER_ID не задан — приветствие не уходит."""
    mock_send = AsyncMock()

    await bot_module.send_startup_greeting(bot_module.conn, admin_peer_id=None, send_func=mock_send)
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_greeting_sent_when_db_empty(bot_module):
    """Пустая БД + задан ADMIN_PEER_ID → приветствие уходит."""
    mock_send = AsyncMock()

    await bot_module.send_startup_greeting(bot_module.conn, admin_peer_id=462149562, send_func=mock_send)
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args.kwargs
    assert call_kwargs["peer_id"] == 462149562
    assert "Бот запустился" in call_kwargs["message"]
    assert "/group" in call_kwargs["message"]


@pytest.mark.asyncio
async def test_greeting_handles_send_error(bot_module):
    """Ошибка отправки не валит бота."""
    mock_send = AsyncMock(side_effect=Exception("VK API down"))

    # Не должно упасть
    await bot_module.send_startup_greeting(bot_module.conn, admin_peer_id=462149562, send_func=mock_send)
    mock_send.assert_called_once()


# ---------- on_unknown ----------


@pytest.mark.asyncio
async def test_unknown_silent_on_plain_text(bot_module):
    """Обычный текст без / — молчание."""
    m = AsyncMock()
    m.text = "привет"
    await bot_module.on_unknown(m)
    m.answer.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_silent_on_empty(bot_module):
    """Пустой текст — молчание."""
    m = AsyncMock()
    m.text = ""
    await bot_module.on_unknown(m)
    m.answer.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_silent_on_text_with_spaces(bot_module):
    """Текст без / с пробелами — молчание."""
    m = AsyncMock()
    m.text = "  как дела?  "
    await bot_module.on_unknown(m)
    m.answer.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_responds_on_unknown_command(bot_module):
    """Неизвестная команда с / — ответ «Не понимаю»."""
    m = AsyncMock()
    m.text = "/foobar"
    await bot_module.on_unknown(m)
    m.answer.assert_called_once()
    assert "Не понимаю" in m.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_unknown_responds_on_lone_slash(bot_module):
    """Одинокий / — это команда, отвечаем «Не понимаю»."""
    m = AsyncMock()
    m.text = "/"
    await bot_module.on_unknown(m)
    m.answer.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_handles_command_with_spaces(bot_module):
    """`  /foobar  ` — strip, потом проверка, / есть."""
    m = AsyncMock()
    m.text = "  /foobar  "
    await bot_module.on_unknown(m)
    m.answer.assert_called_once()


@pytest.mark.asyncio
async def test_background_checker_only_teachers(bot_module, monkeypatch):
    """Если в БД только преподаватели, цикл не должен падать и должен их проверить."""
    import scheduler

    # Настраиваем моки
    monkeypatch.setattr(bot_module.db, "should_run_daily_cleanup", lambda c: False)

    async def fake_bell(conn, api, **kw):
        pass

    monkeypatch.setattr(bot_module, "run_bell_checks", fake_bell)

    monkeypatch.setattr(scheduler, "refresh_base_info_if_needed", lambda c, b: {"mock": "base"})

    # 0 групп
    monkeypatch.setattr(bot_module.db, "get_all_groups", lambda c, only_active=True: [])
    # 1 препод
    monkeypatch.setattr(
        bot_module.db,
        "get_all_teachers",
        lambda c, only_active=True: [{"peer_id": 2000000002, "teacher_uuid": "uuid-1", "teacher_name": "Юнева"}],
    )

    # Мокаем сам цикл проверки препода
    called_teacher = []

    def fake_teacher_cycle(c, b, uuid, **kw):
        called_teacher.append(uuid)
        return []

    monkeypatch.setattr(scheduler, "run_teacher_check_cycle", fake_teacher_cycle)

    monkeypatch.setattr(bot_module.db, "get_setting", lambda c, k, d: 0)

    # Мокаем asyncio.sleep, чтобы выйти из бесконечного цикла после 1 итерации
    class StopLoop(Exception):
        pass

    async def fake_sleep(seconds):
        raise StopLoop()

    monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)

    # Запускаем
    try:
        await bot_module.background_checker()
    except StopLoop:
        pass

    assert "uuid-1" in called_teacher


@pytest.mark.asyncio
async def test_background_checker_only_groups(bot_module, monkeypatch):
    """Если в БД только группы, преподаватели не должны ломать цикл."""
    import scheduler

    monkeypatch.setattr(bot_module.db, "should_run_daily_cleanup", lambda c: False)

    async def fake_bell(conn, api, **kw):
        pass

    monkeypatch.setattr(bot_module, "run_bell_checks", fake_bell)
    monkeypatch.setattr(scheduler, "refresh_base_info_if_needed", lambda c, b: {"mock": "base"})

    # 1 группа
    monkeypatch.setattr(
        bot_module.db,
        "get_all_groups",
        lambda c, only_active=True: [{"peer_id": 2000000002, "group_uuid": "grp-1", "group_name": "419"}],
    )
    # 0 преподавателей
    monkeypatch.setattr(bot_module.db, "get_all_teachers", lambda c, only_active=True: [])

    called_group = []

    def fake_group_cycle(c, b, uuid, **kw):
        called_group.append(uuid)
        return []

    monkeypatch.setattr(scheduler, "run_check_cycle", fake_group_cycle)

    monkeypatch.setattr(bot_module.db, "get_setting", lambda c, k, d: 0)

    class StopLoop(Exception):
        pass

    async def fake_sleep(seconds):
        raise StopLoop()

    monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)

    try:
        await bot_module.background_checker()
    except StopLoop:
        pass

    assert "grp-1" in called_group
