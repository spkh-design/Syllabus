"""Тесты для handlers.py.

Message заменён на простой stub с методом answer().
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import database as db
import handlers
import schedule_api


class FakeMessage:
    """Заглушка vkbottle.bot.Message."""

    def __init__(self, peer_id: int = 123, text: str = ""):
        self.peer_id = peer_id
        self.text = text
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "h.db")
    yield c
    c.close()


@pytest.fixture
def base_info():
    return {
        "divisions": [
            {"name": "(СП-4) Энергетическое отделение", "id": "div-sp4"},
        ],
        "groups": [
            {"name": "419", "id": "grp-419", "division": "div-sp4", "curse": 3},
        ],
        "teachers": [], "disciplines": [],
        "lesson_Types": [], "audithories": [], "territories": [],
    }


@pytest.fixture
def patch_base(monkeypatch, base_info):
    """Подменяем _load_base, чтобы не ходить в API."""
    monkeypatch.setattr(handlers, "_load_base", lambda c: base_info)
    return base_info


# ---------- /start ----------

@pytest.mark.asyncio
async def test_start(conn):
    m = FakeMessage()
    await handlers.cmd_start(m, conn)
    assert len(m.answers) == 1
    assert "Привет" in m.answers[0]
    assert "/group" in m.answers[0]


# ---------- /group ----------

@pytest.mark.asyncio
async def test_group_success(conn, patch_base):
    m = FakeMessage(peer_id=555)
    await handlers.cmd_group(m, conn, "СП-4 419")
    assert "Настроено" in m.answers[-1]
    assert db.get_setting(conn, "group_name") == "419"
    assert db.get_setting(conn, "group_uuid") == "grp-419"
    assert db.get_setting(conn, "division") == "СП-4"
    assert db.get_setting(conn, "peer_id") == "555"


@pytest.mark.asyncio
async def test_group_wrong_format(conn):
    m = FakeMessage()
    await handlers.cmd_group(m, conn, "419")  # без подразделения
    assert "Формат" in m.answers[-1]


@pytest.mark.asyncio
async def test_group_not_found(conn, patch_base):
    m = FakeMessage()
    await handlers.cmd_group(m, conn, "СП-2 999")
    assert "не найдена" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_group_no_base(conn, monkeypatch):
    monkeypatch.setattr(handlers, "_load_base", lambda c: None)
    m = FakeMessage()
    await handlers.cmd_group(m, conn, "СП-4 419")
    assert "справочники" in m.answers[-1].lower() or "не удалось" in m.answers[-1].lower()


# ---------- /where ----------

@pytest.mark.asyncio
async def test_where_not_configured(conn):
    m = FakeMessage()
    await handlers.cmd_where(m, conn)
    assert "не настроена" in m.answers[-1]


@pytest.mark.asyncio
async def test_where_configured(conn):
    db.set_setting(conn, "group_name", "419")
    db.set_setting(conn, "division", "СП-4")
    m = FakeMessage()
    await handlers.cmd_where(m, conn)
    assert "419" in m.answers[-1]
    assert "СП-4" in m.answers[-1]


# ---------- /today, /tomorrow ----------

@pytest.mark.asyncio
async def test_today_no_group(conn):
    m = FakeMessage()
    await handlers.cmd_today(m, conn)
    assert "Настройте группу" in m.answers[-1] or \
           "настройте группу" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_today_success(conn, patch_base, monkeypatch):
    db.set_setting(conn, "group_uuid", "grp-419")
    lessons = [{"number": 1, "subgroup": 0, "discipline": "Web",
                "teacher": "T", "auditoria": "43", "lesson_type": "Лекция"}]
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: lessons)
    m = FakeMessage()
    await handlers.cmd_today(m, conn)
    assert "Web" in m.answers[-1]


@pytest.mark.asyncio
async def test_today_api_error(conn, patch_base, monkeypatch):
    db.set_setting(conn, "group_uuid", "grp-419")

    def boom(*a, **kw):
        raise schedule_api.ScheduleAPIError("down")
    monkeypatch.setattr(schedule_api, "get_group_lessons", boom)

    m = FakeMessage()
    await handlers.cmd_today(m, conn)
    assert "не удалось" in m.answers[-1].lower()


# ---------- /status ----------

@pytest.mark.asyncio
async def test_status_empty(conn):
    m = FakeMessage()
    await handlers.cmd_status(m, conn)
    text = m.answers[-1]
    assert "не настроена" in text
    assert "Справочники" in text


@pytest.mark.asyncio
async def test_status_full(conn):
    db.set_setting(conn, "group_name", "419")
    db.set_setting(conn, "division", "СП-4")
    db.set_setting(conn, "peer_id", "123")
    db.save_base_info(conn, {"x": 1})
    m = FakeMessage()
    await handlers.cmd_status(m, conn)
    text = m.answers[-1]
    assert "419" in text
    assert "СП-4" in text


# ---------- /unsubscribe ----------

@pytest.mark.asyncio
async def test_unsubscribe(conn):
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "123")
    m = FakeMessage()
    await handlers.cmd_unsubscribe(m, conn)
    assert db.get_setting(conn, "group_uuid") is None
    assert db.get_setting(conn, "peer_id") is None
    assert "Отписка" in m.answers[-1]