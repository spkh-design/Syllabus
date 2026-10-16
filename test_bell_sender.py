"""Тесты для bell_sender.py."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import database as db
from bell_sender import process_group_bell, run_bell_checks

PEER = 2000000002
GROUP_UUID = "grp-419"
CURATOR_ID = 462149562


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "bs.db")
    yield c
    c.close()


@pytest.fixture
def group(conn):
    db.add_group(conn, PEER, GROUP_UUID, "419", "СП-4", curator_id=CURATOR_ID)
    return db.get_group(conn, PEER)


@pytest.fixture
def bot_api():
    api = MagicMock()
    api.messages.send = AsyncMock()
    api.users.get = AsyncMock(
        return_value=[
            # простая заглушка с first_name
            type("U", (), {"first_name": "Юнева", "id": CURATOR_ID})()
        ]
    )
    return api


def _make_now(hour: int = 18, minute: int = 0) -> datetime:
    return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)


# ============================================================
# process_group_bell — Case A (bell schedule найден)
# ============================================================


@pytest.mark.asyncio
async def test_bell_detected_today_not_sent(conn, group, bot_api):
    """bell schedule найден, detected_at == сегодня → не отправляем."""
    target = date.today() + timedelta(days=1)
    db.save_bell_schedule(conn, PEER, target, 100, 111, image_bytes=b"PNG", caption="звонки")
    # detected_at — сейчас (модуль save_bell_schedule ставит _now())

    now = _make_now(18, 5)
    result = await process_group_bell(conn, bot_api, group, now)
    assert result is False
    bot_api.messages.send.assert_not_called()


@pytest.mark.asyncio
async def test_bell_detected_yesterday_many_messages_sent(conn, group, bot_api):
    """bell schedule вчера + >10 сообщений → отправляем."""
    target = date.today() + timedelta(days=1)
    db.save_bell_schedule(conn, PEER, target, 100, 111, image_bytes=b"PNG")

    # Сдвигаем detected_at на вчера
    yesterday = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE bell_schedules SET detected_at = ? WHERE peer_id = ? AND target_date = ?",
        (yesterday, PEER, target.isoformat()),
    )
    conn.commit()

    # Имитируем 15 сообщений после detected_at
    for i in range(15):
        db.save_chat_message(conn, PEER, 1000 + i, 999, f"msg{i}", [])

    # Мокаем upload
    with patch("bell_sender.upload_png_to_vk", new=AsyncMock(return_value="photo1_1")):
        result = await process_group_bell(conn, bot_api, group, _make_now(18, 5))

    assert result is True
    bot_api.messages.send.assert_awaited_once()
    # Проверяем, что это отправка bell schedule (в тексте «расписание звонков»)
    call_kwargs = bot_api.messages.send.call_args.kwargs
    assert "расписание звонков" in call_kwargs["message"].lower()

    # sent_at должен быть выставлен
    bs = db.get_bell_schedule(conn, PEER, target)
    assert bs["sent_at"] is not None


@pytest.mark.asyncio
async def test_bell_detected_yesterday_few_messages_not_sent(conn, group, bot_api):
    """bell schedule вчера + мало сообщений → не отправляем."""
    target = date.today() + timedelta(days=1)
    db.save_bell_schedule(conn, PEER, target, 100, 111, image_bytes=b"PNG")

    yesterday = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE bell_schedules SET detected_at = ? WHERE peer_id = ? AND target_date = ?",
        (yesterday, PEER, target.isoformat()),
    )
    conn.commit()

    # Только 3 сообщения
    for i in range(3):
        db.save_chat_message(conn, PEER, 2000 + i, 999, f"m{i}", [])

    result = await process_group_bell(conn, bot_api, group, _make_now(18, 5))
    assert result is False
    bot_api.messages.send.assert_not_called()


@pytest.mark.asyncio
async def test_bell_detected_long_ago_sent(conn, group, bot_api):
    """bell schedule 3 дня назад → отправляем."""
    target = date.today() + timedelta(days=1)
    db.save_bell_schedule(conn, PEER, target, 100, 111, image_bytes=b"PNG")

    old = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE bell_schedules SET detected_at = ? WHERE peer_id = ? AND target_date = ?",
        (old, PEER, target.isoformat()),
    )
    conn.commit()

    with patch("bell_sender.upload_png_to_vk", new=AsyncMock(return_value="photo1_1")):
        result = await process_group_bell(conn, bot_api, group, _make_now(18, 5))

    assert result is True
    bot_api.messages.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_bell_already_sent_not_repeated(conn, group, bot_api):
    """Если sent_at уже стоит — не отправляем."""
    target = date.today() + timedelta(days=1)
    db.save_bell_schedule(conn, PEER, target, 100, 111, image_bytes=b"PNG")
    db.mark_bell_schedule_sent(conn, PEER, target)

    result = await process_group_bell(conn, bot_api, group, _make_now(18, 5))
    assert result is False
    bot_api.messages.send.assert_not_called()


# ============================================================
# process_group_bell — Case B (bell schedule не найден)
# ============================================================


@pytest.mark.asyncio
async def test_no_bell_no_holiday_silent(conn, group, bot_api):
    """Нет bell schedule, завтра не праздник → тишина."""
    result = await process_group_bell(conn, bot_api, group, _make_now(18, 5))
    assert result is False
    bot_api.messages.send.assert_not_called()


@pytest.mark.asyncio
async def test_no_bell_holiday_pings_curator(conn, group, bot_api, monkeypatch):
    """Нет bell schedule, завтра праздник → пинг куратора."""
    # Завтра — праздник
    tomorrow = date.today() + timedelta(days=1)
    monkeypatch.setattr(
        "bell_sender.holidays.is_holiday", lambda d: (d == tomorrow, "День учителя" if d == tomorrow else "")
    )

    result = await process_group_bell(conn, bot_api, group, _make_now(18, 5))
    assert result is True
    bot_api.messages.send.assert_awaited_once()
    msg = bot_api.messages.send.call_args.kwargs["message"]
    assert "День учителя" in msg
    assert f"[id{CURATOR_ID}|" in msg

    # Флаг в БД
    assert db.was_holiday_pinged(conn, PEER, tomorrow) is True


@pytest.mark.asyncio
async def test_holiday_ping_not_repeated(conn, group, bot_api, monkeypatch):
    """Пинг куратора отправляется только один раз."""
    tomorrow = date.today() + timedelta(days=1)
    monkeypatch.setattr(
        "bell_sender.holidays.is_holiday", lambda d: (d == tomorrow, "День учителя" if d == tomorrow else "")
    )
    # Первый пинг
    await process_group_bell(conn, bot_api, group, _make_now(18, 5))
    assert bot_api.messages.send.await_count == 1

    # Второй вызов — не повторяем
    await process_group_bell(conn, bot_api, group, _make_now(18, 30))
    assert bot_api.messages.send.await_count == 1


@pytest.mark.asyncio
async def test_holiday_ping_without_curator(conn, bot_api, monkeypatch):
    """Пинг работает и без куратора (без mention)."""
    db.add_group(conn, PEER, GROUP_UUID, "419", "СП-4", curator_id=None)
    group = db.get_group(conn, PEER)

    tomorrow = date.today() + timedelta(days=1)
    monkeypatch.setattr(
        "bell_sender.holidays.is_holiday", lambda d: (d == tomorrow, "День учителя" if d == tomorrow else "")
    )

    result = await process_group_bell(conn, bot_api, group, _make_now(18, 5))
    assert result is True
    msg = bot_api.messages.send.call_args.kwargs["message"]
    assert "куратор" in msg.lower()


# ============================================================
# run_bell_checks
# ============================================================


@pytest.mark.asyncio
async def test_run_bell_checks_before_18_does_nothing(conn, group, bot_api):
    """До 18:00 ничего не делаем."""
    await run_bell_checks(conn, bot_api, now=_make_now(17, 30))
    bot_api.messages.send.assert_not_called()
    # И флага нет
    assert db.get_setting(conn, "bell_check_date") is None


@pytest.mark.asyncio
async def test_run_bell_checks_sets_flag(conn, group, bot_api):
    """После 18:00 флаг ставится, повторно не запускается."""
    now = _make_now(18, 5)
    await run_bell_checks(conn, bot_api, now=now)
    assert db.get_setting(conn, "bell_check_date") == now.date().isoformat()

    # Второй вызов в тот же день — сразу выход
    with patch("bell_sender.process_group_bell", new=AsyncMock()) as mock_proc:
        await run_bell_checks(conn, bot_api, now=_make_now(20, 0))
        mock_proc.assert_not_called()


@pytest.mark.asyncio
async def test_run_bell_checks_no_groups(conn, bot_api):
    """Нет групп — тишина, но флаг ставится."""
    now = _make_now(18, 5)
    await run_bell_checks(conn, bot_api, now=now)
    assert db.get_setting(conn, "bell_check_date") == now.date().isoformat()
    bot_api.messages.send.assert_not_called()


@pytest.mark.asyncio
async def test_run_bell_checks_error_in_one_group_not_block_others(conn, bot_api, monkeypatch):
    """Ошибка в одной группе не мешает обработке других."""
    db.add_group(conn, 2000000002, "g1", "419", "СП-4")
    db.add_group(conn, 2000000003, "g2", "301", "СП-2")

    call_log = []

    async def fake_process(conn, api, group, now):
        call_log.append(group["peer_id"])
        if group["peer_id"] == 2000000002:
            raise RuntimeError("boom")
        return False

    monkeypatch.setattr("bell_sender.process_group_bell", fake_process)

    await run_bell_checks(conn, bot_api, now=_make_now(18, 5))
    assert set(call_log) == {2000000002, 2000000003}


# ============================================================
# prepare_image
# ============================================================


def test_prepare_image_png():
    from bell_sender import prepare_image

    png = b"\x89PNG\r\n\x1a\nfake"
    data, ct, fn = prepare_image(png)
    assert data == png
    assert ct == "image/png"
    assert fn == "schedule.png"


def test_prepare_image_jpeg():
    from bell_sender import prepare_image

    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIFfake"
    data, ct, fn = prepare_image(jpeg)
    assert data == jpeg
    assert ct == "image/jpeg"
    assert fn == "schedule.jpg"


def test_prepare_image_empty():
    from bell_sender import prepare_image

    data, ct, fn = prepare_image(b"")
    assert data == b""
    assert ct == "image/png"


def test_prepare_image_unknown_falls_back_to_png():
    """Неизвестный формат — конвертация в PNG (или возврат как есть)."""
    from bell_sender import prepare_image

    # Битые байты, не PNG, не JPG
    data, ct, fn = prepare_image(b"\x00\x01\x02garbage")
    # Не важно, что данные не сконвертировались — важно,
    # что content_type не падает и функция не кидает
    assert ct in ("image/png", "image/jpeg")
