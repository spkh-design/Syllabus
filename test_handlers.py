"""Тесты для handlers.py.

Message заменён на простой stub с методом answer().
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from unittest.mock import MagicMock

import database as db
import handlers
import schedule_api


class FakeMessage:
    def __init__(self, peer_id=123, text="", from_id=None):
        self.peer_id = peer_id
        self.text = text
        self.from_id = from_id if from_id is not None else peer_id
        self.answers: list[str] = []
        self.attachments: list[str | None] = []
        self.ctx_api = MagicMock()

    async def answer(self, text, attachment=None):
        self.answers.append(text)
        self.attachments.append(attachment)


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "h.db")
    yield c
    c.close()


@pytest.fixture
def base_info():
    return {
        "divisions": [{"name": "(СП-4) Энергетическое отделение", "id": "div-sp4"}],
        "groups": [{"name": "419", "id": "grp-419", "division": "div-sp4", "curse": 3}],
        "teachers": [],
        "disciplines": [],
        "lesson_Types": [],
        "audithories": [],
        "territories": [],
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
    text = m.answers[0]
    assert "Привет" in text
    assert "/week" in text
    assert "/nextweek" in text
    assert "/status" in text
    assert "Админ-команды" in text


# ---------- /group ----------


@pytest.mark.asyncio
async def test_group_success(admin_ids, conn, patch_base):
    m = FakeMessage(peer_id=555, from_id=123)
    await handlers.cmd_group(m, conn, "СП-4 419")
    assert "Настроено" in m.answers[-1]
    assert db.get_setting(conn, "group_name") == "419"
    assert db.get_setting(conn, "group_uuid") == "grp-419"
    assert db.get_setting(conn, "division") == "СП-4"
    assert db.get_setting(conn, "peer_id") == "555"


@pytest.mark.asyncio
async def test_group_wrong_format(admin_ids, conn):
    m = FakeMessage(from_id=123)
    await handlers.cmd_group(m, conn, "419")
    assert "Формат" in m.answers[-1]


@pytest.mark.asyncio
async def test_group_not_found(admin_ids, conn, patch_base):
    m = FakeMessage(from_id=123)
    await handlers.cmd_group(m, conn, "СП-2 999")
    assert "не найдена" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_group_no_base(admin_ids, conn, monkeypatch):
    monkeypatch.setattr(handlers, "_load_base", lambda c: None)
    m = FakeMessage(from_id=123)
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
    assert "не настроена" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_today_success(conn, patch_base, monkeypatch):
    db.set_setting(conn, "group_uuid", "grp-419")
    lessons = [
        {"number": 1, "subgroup": 0, "discipline": "Web", "teacher": "T", "auditoria": "43", "lesson_type": "Лекция"}
    ]
    monkeypatch.setattr(schedule_api, "get_group_lessons", lambda b, g, d: lessons)

    calls = []

    async def fake_send(api, peer_id, msg, **kw):
        calls.append(msg)

    monkeypatch.setattr(handlers, "send_schedule_message", fake_send)

    m = FakeMessage()
    await handlers.cmd_today(m, conn)
    assert len(calls) == 1
    assert calls[0].kind == "day_image"


@pytest.mark.asyncio
async def test_today_api_error(conn, patch_base, monkeypatch):
    db.set_setting(conn, "group_uuid", "grp-419")

    def boom(*a, **kw):
        raise schedule_api.ScheduleAPIError("down")

    monkeypatch.setattr(schedule_api, "get_group_lessons", boom)

    m = FakeMessage()
    await handlers.cmd_today(m, conn)
    assert "не удалось" in m.answers[-1].lower()


# ---------- /week и /nextweek ----------


@pytest.mark.asyncio
async def test_week_current(conn, patch_base, monkeypatch):
    """`/week` показывает ТЕКУЩУЮ неделю (понедельник — воскресенье)."""
    db.set_setting(conn, "group_uuid", "grp-419")

    captured = {}

    def fake_get_week(base, g, d):
        captured["monday"] = d
        return {d + timedelta(days=i): [] for i in range(7)}

    monkeypatch.setattr(schedule_api, "get_week_lessons", fake_get_week)

    async def fake_send(api, peer_id, msg, **kw):
        pass

    monkeypatch.setattr(handlers, "send_schedule_message", fake_send)

    m = FakeMessage()
    await handlers.cmd_week(m, conn)

    today = date.today()
    expected_monday = today - timedelta(days=today.weekday())
    assert captured["monday"] == expected_monday


@pytest.mark.asyncio
async def test_nextweek(conn, patch_base, monkeypatch):
    """`/nextweek` показывает СЛЕДУЮЩУЮ неделю."""
    db.set_setting(conn, "group_uuid", "grp-419")

    captured = {}

    def fake_get_week(base, g, d):
        captured["monday"] = d
        return {d + timedelta(days=i): [] for i in range(7)}

    monkeypatch.setattr(schedule_api, "get_week_lessons", fake_get_week)

    async def fake_send(api, peer_id, msg, **kw):
        pass

    monkeypatch.setattr(handlers, "send_schedule_message", fake_send)

    m = FakeMessage()
    await handlers.cmd_nextweek(m, conn)

    today = date.today()
    expected_monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    assert captured["monday"] == expected_monday


@pytest.mark.asyncio
async def test_week_no_group(conn):
    m = FakeMessage()
    await handlers.cmd_week(m, conn)
    assert "не настроена" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_nextweek_no_group(conn):
    m = FakeMessage()
    await handlers.cmd_nextweek(m, conn)
    assert "не настроена" in m.answers[-1].lower()


# ---------- /status ----------


@pytest.mark.asyncio
async def test_status_empty(admin_ids, conn):
    m = FakeMessage(from_id=123)
    await handlers.cmd_status(m, conn)
    text = m.answers[-1]
    assert "не настроена" in text
    assert "Справочники" in text


@pytest.mark.asyncio
async def test_status_full(admin_ids, conn):
    db.set_setting(conn, "group_name", "419")
    db.set_setting(conn, "division", "СП-4")
    db.set_setting(conn, "peer_id", "123")
    db.save_base_info(conn, {"x": 1})
    m = FakeMessage(from_id=123)
    await handlers.cmd_status(m, conn)
    text = m.answers[-1]
    assert "419" in text
    assert "СП-4" in text


@pytest.mark.asyncio
async def test_status_denied_for_non_admin(admin_ids, conn):
    m = FakeMessage(from_id=999)
    await handlers.cmd_status(m, conn)
    assert "нет прав" in m.answers[-1].lower()


# ---------- /unsubscribe ----------


@pytest.mark.asyncio
async def test_unsubscribe(admin_ids, conn):
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "123")
    m = FakeMessage(from_id=123)
    await handlers.cmd_unsubscribe(m, conn)
    assert db.get_setting(conn, "group_uuid") is None
    assert db.get_setting(conn, "peer_id") is None
    assert "Отписка" in m.answers[-1]


# ---------- /week_skip ----------


@pytest.mark.asyncio
async def test_week_skip_no_group(admin_ids, conn):
    m = FakeMessage(from_id=123, text="/week_skip")
    await handlers.cmd_week_skip(m, conn)
    assert "Настройте группу" in m.answers[-1] or "настройте группу" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_week_skip_default_next_week(admin_ids, conn):
    db.set_setting(conn, "group_uuid", "grp-419")
    m = FakeMessage(from_id=123)
    await handlers.cmd_week_skip(m, conn)  # args по умолчанию ""
    assert "помечена" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_week_skip_explicit_date(admin_ids, conn):
    db.set_setting(conn, "group_uuid", "grp-419")
    m = FakeMessage(from_id=123)
    await handlers.cmd_week_skip(m, conn, "21.09.2026")
    row = db.get_week_announced(conn, "grp-419", date(2026, 9, 21))
    assert row is not None
    assert row["is_full"] is False


@pytest.mark.asyncio
async def test_week_skip_wrong_format(admin_ids, conn):
    db.set_setting(conn, "group_uuid", "grp-419")
    m = FakeMessage(from_id=123)
    await handlers.cmd_week_skip(m, conn, "сегодня")
    assert "Формат" in m.answers[-1] or "❌" in m.answers[-1]


@pytest.fixture
def admin_ids(monkeypatch):
    """Все тесты считают текущего пользователя админом."""
    import config

    monkeypatch.setattr(config, "ADMIN_USER_IDS", [123])
    # handlers импортирует is_admin_id напрямую, поэтому правим и там
    import handlers

    monkeypatch.setattr(handlers, "is_admin_id", lambda uid: uid == 123)


# ---------- is_admin ----------


def test_is_admin_true(admin_ids):
    m = FakeMessage(from_id=123)
    assert handlers.is_admin(m) is True


def test_is_admin_false(admin_ids):
    m = FakeMessage(from_id=999)
    assert handlers.is_admin(m) is False


# ---------- _require_admin ----------


@pytest.mark.asyncio
async def test_require_admin_ok(admin_ids, conn):
    m = FakeMessage(from_id=123)
    assert await handlers._require_admin(m) is True
    assert m.answers == []


@pytest.mark.asyncio
async def test_require_admin_denied(admin_ids, conn):
    m = FakeMessage(from_id=999)
    assert await handlers._require_admin(m) is False
    assert "нет прав" in m.answers[-1].lower()


# ---------- Админ-команды отказывают не-админам ----------


@pytest.mark.asyncio
async def test_group_denied_for_non_admin(admin_ids, conn, patch_base):
    m = FakeMessage(from_id=999)
    await handlers.cmd_group(m, conn, "СП-4 419")
    assert "нет прав" in m.answers[-1].lower()
    # настройки не сохранились
    assert db.get_setting(conn, "group_uuid") is None


@pytest.mark.asyncio
async def test_interval_denied_for_non_admin(admin_ids, conn):
    m = FakeMessage(from_id=999)
    await handlers.cmd_interval(m, conn, "15")
    assert "нет прав" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_unsubscribe_denied_for_non_admin(admin_ids, conn):
    db.set_setting(conn, "group_uuid", "grp-419")
    m = FakeMessage(from_id=999)
    await handlers.cmd_unsubscribe(m, conn)
    assert "нет прав" in m.answers[-1].lower()
    # настройки НЕ удалены
    assert db.get_setting(conn, "group_uuid") == "grp-419"


@pytest.mark.asyncio
async def test_metrics_denied_for_non_admin(admin_ids, conn):
    m = FakeMessage(from_id=999)
    await handlers.cmd_metrics(m, conn)
    assert "нет прав" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_subscribers_denied_for_non_admin(admin_ids, conn):
    m = FakeMessage(from_id=999)
    await handlers.cmd_subscribers(m, conn)
    assert "нет прав" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_test_denied_for_non_admin(admin_ids, conn):
    m = FakeMessage(from_id=999)
    await handlers.cmd_test(m, conn)
    assert "нет прав" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_week_skip_denied_for_non_admin(admin_ids, conn):
    db.set_setting(conn, "group_uuid", "grp-419")
    m = FakeMessage(from_id=999, text="/week_skip")
    await handlers.cmd_week_skip(m, conn)
    assert "нет прав" in m.answers[-1].lower()


# ---------- Публичные команды работают для всех ----------


@pytest.mark.asyncio
async def test_start_public(admin_ids, conn):
    m = FakeMessage(from_id=999)
    await handlers.cmd_start(m, conn)
    assert "Привет" in m.answers[-1]


@pytest.mark.asyncio
async def test_where_public(admin_ids, conn):
    m = FakeMessage(from_id=999)
    await handlers.cmd_where(m, conn)
    # Просто не должно быть "нет прав"
    assert "нет прав" not in m.answers[-1].lower()


# ---------- /whoami ----------


@pytest.mark.asyncio
async def test_whoami_admin(admin_ids, conn):
    m = FakeMessage(from_id=123, peer_id=-100500)
    await handlers.cmd_whoami(m, conn)
    text = m.answers[-1]
    assert "123" in text
    assert "-100500" in text
    assert "✅ да" in text


@pytest.mark.asyncio
async def test_whoami_non_admin(admin_ids, conn):
    m = FakeMessage(from_id=999, peer_id=-100500)
    await handlers.cmd_whoami(m, conn)
    text = m.answers[-1]
    assert "999" in text
    assert "❌ нет" in text
