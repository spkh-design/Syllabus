"""Тесты для команды /bell."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import database as db
import handlers

PM_PEER = 462149562  # ЛС
CHAT_PEER = 2000000002  # беседа


class FakeMessage:
    def __init__(self, peer_id: int = CHAT_PEER, text: str = "", from_id: int | None = None):
        self.peer_id = peer_id
        self.text = text
        self.from_id = from_id if from_id is not None else peer_id
        self.answers: list[str] = []
        self.ctx_api = MagicMock()
        self.ctx_api.users.get = AsyncMock(return_value=[])
        self.ctx_api.messages.send = AsyncMock()

    async def answer(self, text, attachment=None):
        self.answers.append(text)


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "bell.db")
    yield c
    c.close()


@pytest.fixture
def group(conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    return db.get_group(conn, CHAT_PEER)


def _fmt(target: date) -> str:
    return target.strftime("%d.%m.%y")


# ============================================================
# Валидация аргументов
# ============================================================


@pytest.mark.asyncio
async def test_bell_no_args(conn, group):
    m = FakeMessage()
    await handlers.cmd_bell(m, conn, "")
    assert any("Формат" in a for a in m.answers)


@pytest.mark.asyncio
async def test_bell_invalid_date(conn, group):
    m = FakeMessage()
    await handlers.cmd_bell(m, conn, "не-дата")
    assert any("распознать дату" in a for a in m.answers)


@pytest.mark.asyncio
async def test_bell_past_date(conn, group):
    m = FakeMessage()
    yesterday = date.today() - timedelta(days=1)
    await handlers.cmd_bell(m, conn, _fmt(yesterday))
    assert any("День уже прошёл" in a for a in m.answers)


@pytest.mark.asyncio
async def test_bell_too_far(conn, group):
    m = FakeMessage()
    far = date.today() + timedelta(days=40)
    await handlers.cmd_bell(m, conn, _fmt(far))
    assert any("Слишком далеко" in a for a in m.answers)


# ============================================================
# Группа не зарегистрирована
# ============================================================


@pytest.mark.asyncio
async def test_bell_no_group(conn):
    m = FakeMessage()
    target = date.today() + timedelta(days=1)
    await handlers.cmd_bell(m, conn, _fmt(target))
    assert any("не зарегистрирована" in a.lower() for a in m.answers)


@pytest.mark.asyncio
async def test_bell_pm_without_session(conn, group):
    m = FakeMessage(peer_id=PM_PEER)
    target = date.today() + timedelta(days=1)
    await handlers.cmd_bell(m, conn, _fmt(target))
    text = "\n".join(m.answers).lower()
    assert "peer=" in text or "/use" in text


# ============================================================
# Case C: запись не найдена — «без изменений»
# ============================================================


@pytest.mark.asyncio
async def test_bell_no_schedule(conn, group):
    m = FakeMessage()
    target = date.today() + timedelta(days=1)
    await handlers.cmd_bell(m, conn, _fmt(target))

    m.ctx_api.messages.send.assert_awaited_once()
    kwargs = m.ctx_api.messages.send.call_args.kwargs
    assert kwargs["peer_id"] == CHAT_PEER
    assert "Без изменений" in kwargs["message"]
    assert _fmt(target) in kwargs["message"]


# ============================================================
# Case A: с картинкой
# ============================================================


@pytest.mark.asyncio
async def test_bell_with_image(conn, group):
    target = date.today() + timedelta(days=1)
    png = b"\x89PNG\r\n\x1a\nfake-bytes"
    db.save_bell_schedule(conn, CHAT_PEER, target, 100, 111, image_bytes=png, caption="звонки")

    m = FakeMessage()
    with patch("handlers.upload_png_to_vk", new=AsyncMock(return_value="photo1_1")):
        await handlers.cmd_bell(m, conn, _fmt(target))

    m.ctx_api.messages.send.assert_awaited_once()
    kwargs = m.ctx_api.messages.send.call_args.kwargs
    assert kwargs["peer_id"] == CHAT_PEER
    assert "🔔" in kwargs["message"]
    assert kwargs["attachment"] == "photo1_1"


@pytest.mark.asyncio
async def test_bell_upload_fails_fallback(conn, group):
    target = date.today() + timedelta(days=1)
    db.save_bell_schedule(conn, CHAT_PEER, target, 100, 111, image_bytes=b"\x89PNG\r\n\x1a\nx", caption="смотрите")

    m = FakeMessage()
    with patch("handlers.upload_png_to_vk", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await handlers.cmd_bell(m, conn, _fmt(target))

    m.ctx_api.messages.send.assert_awaited_once()
    kwargs = m.ctx_api.messages.send.call_args.kwargs
    assert "🔔" in kwargs["message"]
    assert "смотрите" in kwargs["message"]
    assert "attachment" not in kwargs


# ============================================================
# Case B: с подписью без картинки
# ============================================================


@pytest.mark.asyncio
async def test_bell_caption_no_image(conn, group):
    target = date.today() + timedelta(days=1)
    db.save_bell_schedule(conn, CHAT_PEER, target, 100, 111, image_bytes=None, caption="звонки: 1 пара 08:30")

    m = FakeMessage()
    await handlers.cmd_bell(m, conn, _fmt(target))

    m.ctx_api.messages.send.assert_awaited_once()
    kwargs = m.ctx_api.messages.send.call_args.kwargs
    assert "Расписание звонков" in kwargs["message"]
    assert "звонки: 1 пара 08:30" in kwargs["message"]
    assert "attachment" not in kwargs


@pytest.mark.asyncio
async def test_bell_caption_empty(conn, group):
    target = date.today() + timedelta(days=1)
    db.save_bell_schedule(conn, CHAT_PEER, target, 100, 111, image_bytes=None, caption="")

    m = FakeMessage()
    await handlers.cmd_bell(m, conn, _fmt(target))

    kwargs = m.ctx_api.messages.send.call_args.kwargs
    assert "текст сообщения отсутствует" in kwargs["message"]


# ============================================================
# peer=N в ЛС
# ============================================================


@pytest.mark.asyncio
async def test_bell_in_pm_with_peer(conn, group):
    target = date.today() + timedelta(days=1)
    m = FakeMessage(peer_id=PM_PEER)
    await handlers.cmd_bell(m, conn, f"{_fmt(target)} peer={CHAT_PEER}")

    kwargs = m.ctx_api.messages.send.call_args.kwargs
    assert kwargs["peer_id"] == CHAT_PEER
    assert "Без изменений" in kwargs["message"]


# ============================================================
# Разные форматы дат
# ============================================================


@pytest.mark.asyncio
async def test_bell_date_word_form(conn, group):
    """Дата словами: «21 октября»."""
    # Берём фиксированную «сегодня», чтобы не зависеть от реальной даты
    today = date.today()
    # Считаем «завтра в словесной форме» — не всегда возможно точно
    # (если сегодня 31 декабря — «завтра» уже следующий год).
    # Проще: тестируем через сегодня+1, но если это перескочит — ок.
    tomorrow = today + timedelta(days=1)
    months = {
        1: "января",
        2: "февраля",
        3: "марта",
        4: "апреля",
        5: "мая",
        6: "июня",
        7: "июля",
        8: "августа",
        9: "сентября",
        10: "октября",
        11: "ноября",
        12: "декабря",
    }
    date_str = f"{tomorrow.day} {months[tomorrow.month]}"
    m = FakeMessage()
    await handlers.cmd_bell(m, conn, date_str)

    m.ctx_api.messages.send.assert_awaited_once()
    kwargs = m.ctx_api.messages.send.call_args.kwargs
    assert _fmt(tomorrow) in kwargs["message"]
