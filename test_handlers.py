"""Тесты для handlers.py. Multi-tenant версия."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

import database as db
import handlers
import schedule_api

# По умолчанию peer_id=123 — это ЛС (0 < peer_id < 2_000_000_000).
# Для «беседы» используем 2_000_000_002.
PM_PEER = 462149562
CHAT_PEER = 2000000002


class FakeMessage:
    def __init__(self, peer_id: int = PM_PEER, text: str = "", from_id: int | None = None):
        self.peer_id = peer_id
        self.text = text
        self.from_id = from_id if from_id is not None else peer_id
        self.answers: list[str] = []
        self.attachments: list[str | None] = []
        self.ctx_api = MagicMock()
        self.ctx_api.users.get = AsyncMock(return_value=[])
        self.ctx_api.messages.send = AsyncMock()

    # Добавляем **kwargs, чтобы принимать keyboard и другие параметры
    async def answer(self, text, attachment=None, **kwargs):
        self.answers.append(text)
        self.attachments.append(attachment)
        self.last_kwargs = kwargs


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
    monkeypatch.setattr(handlers, "_load_base", lambda c: base_info)
    return base_info


@pytest.fixture
def patch_send(monkeypatch):
    """Мок send_schedule_message — собирает вызовы."""
    calls = []

    async def fake_send(api, peer_id, msg, **kw):
        calls.append({"peer_id": peer_id, "msg": msg, **kw})

    monkeypatch.setattr(handlers, "send_schedule_message", fake_send)
    return calls


@pytest.fixture
def admin_ids(monkeypatch):
    """Все тесты считают from_id=123 админом."""
    import config

    monkeypatch.setattr(config, "ADMIN_USER_IDS", [123])
    monkeypatch.setattr(handlers, "is_admin_id", lambda uid: uid == 123)


# ============================================================
# /start
# ============================================================


@pytest.mark.asyncio
async def test_start(conn):
    m = FakeMessage()
    await handlers.cmd_start(m, conn)
    assert len(m.answers) == 1
    text = m.answers[0]
    assert "Привет" in text
    assert "/help" in text
    assert "keyboard" in m.last_kwargs


# ============================================================
# /where
# ============================================================


@pytest.mark.asyncio
async def test_where_not_configured(conn):
    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_where(m, conn)
    assert "не зарегистрирована" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_where_configured(conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_where(m, conn)
    assert "419" in m.answers[-1]
    assert "СП-4" in m.answers[-1]


@pytest.mark.asyncio
async def test_where_in_pm_without_session(conn):
    m = FakeMessage(peer_id=PM_PEER)
    await handlers.cmd_where(m, conn)
    # Подсказка про peer=N или /use
    text = m.answers[-1].lower()
    assert "peer=" in text or "/use" in text


@pytest.mark.asyncio
async def test_where_in_pm_with_session(conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    db.set_user_session(conn, PM_PEER, CHAT_PEER)
    m = FakeMessage(peer_id=PM_PEER)
    await handlers.cmd_where(m, conn)
    assert "419" in m.answers[-1]


# ============================================================
# /group
# ============================================================


@pytest.mark.asyncio
async def test_group_in_chat(admin_ids, conn, patch_base):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_group(m, conn, "СП-4 419")
    assert "Настроено" in m.answers[-1]
    g = db.get_group(conn, CHAT_PEER)
    assert g is not None
    assert g["group_name"] == "419"
    assert g["group_uuid"] == "grp-419"


@pytest.mark.asyncio
async def test_group_in_pm_with_peer(admin_ids, conn, patch_base):
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_group(m, conn, f"СП-4 419 peer={CHAT_PEER}")
    assert "Настроено" in m.answers[-1]
    g = db.get_group(conn, CHAT_PEER)
    assert g is not None


@pytest.mark.asyncio
async def test_group_in_pm_without_peer(admin_ids, conn, patch_base):
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_group(m, conn, "СП-4 419")
    text = m.answers[-1].lower()
    assert "peer=" in text or "/use" in text


@pytest.mark.asyncio
async def test_group_wrong_format(admin_ids, conn, patch_base):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_group(m, conn, "419")
    assert "Формат" in m.answers[-1]


@pytest.mark.asyncio
async def test_group_not_found(admin_ids, conn, patch_base):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_group(m, conn, "СП-2 999")
    assert "не найдена" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_group_no_base(admin_ids, conn, monkeypatch):
    monkeypatch.setattr(handlers, "_load_base", lambda c: None)
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_group(m, conn, "СП-4 419")
    assert "справочники" in m.answers[-1].lower() or "не удалось" in m.answers[-1].lower()


# ============================================================
# /add_group
# ============================================================


@pytest.mark.asyncio
async def test_add_group_in_chat(admin_ids, conn, patch_base):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_add_group(m, conn, "СП-4 419")
    assert "зарегистрирована" in m.answers[-1].lower()
    assert db.get_group(conn, CHAT_PEER) is not None


@pytest.mark.asyncio
async def test_add_group_in_pm_with_peer(admin_ids, conn, patch_base):
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_add_group(m, conn, f"peer={CHAT_PEER} СП-4 419")
    assert "зарегистрирована" in m.answers[-1].lower()
    assert db.get_group(conn, CHAT_PEER) is not None


@pytest.mark.asyncio
async def test_add_group_in_pm_without_peer(admin_ids, conn, patch_base):
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_add_group(m, conn, "СП-4 419")
    assert "peer=" in m.answers[-1]


@pytest.mark.asyncio
async def test_add_group_wrong_format(admin_ids, conn, patch_base):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_add_group(m, conn, "419")
    assert "Формат" in m.answers[-1]


@pytest.mark.asyncio
async def test_add_group_not_found(admin_ids, conn, patch_base):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_add_group(m, conn, "СП-2 999")
    assert "не найдена" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_add_group_denied_for_non_admin(admin_ids, conn, patch_base):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=999)
    await handlers.cmd_add_group(m, conn, "СП-4 419")
    assert "нет прав" in m.answers[-1].lower()


# ============================================================
# /remove_group
# ============================================================


@pytest.mark.asyncio
async def test_remove_group_in_chat(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_remove_group(m, conn, "")
    assert db.get_group(conn, CHAT_PEER) is None
    assert "удалена" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_remove_group_in_pm(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_remove_group(m, conn, f"peer={CHAT_PEER}")
    assert db.get_group(conn, CHAT_PEER) is None


@pytest.mark.asyncio
async def test_remove_group_not_registered(admin_ids, conn):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_remove_group(m, conn, "")
    assert "не зарегистрирована" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_remove_group_in_pm_without_peer(admin_ids, conn):
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_remove_group(m, conn, "")
    assert "peer=" in m.answers[-1]


# ============================================================
# /use
# ============================================================


@pytest.mark.asyncio
async def test_use_in_chat_forbidden(conn):
    """В беседе /use не работает."""
    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_use(m, conn, str(CHAT_PEER))
    assert "только в личных" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_use_in_pm_ok(conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_use(m, conn, str(CHAT_PEER))
    text = m.answers[-1].lower()
    # Работает и для групп, и для преподавателей — проверяем общую формулировку
    assert "активная беседа" in text
    assert "сп-4 / 419" in text
    assert db.get_user_session(conn, 123) == CHAT_PEER


@pytest.mark.asyncio
async def test_use_in_pm_for_teacher(conn):
    """`/use` работает и для беседы преподавателя."""
    db.add_teacher(conn, CHAT_PEER, "uuid-yuneva", "Юнева Наталья Дмитриевна")
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_use(m, conn, str(CHAT_PEER))
    text = m.answers[-1].lower()
    assert "активная беседа" in text
    assert "преподаватель: юнева наталья дмитриевна" in text
    assert db.get_user_session(conn, 123) == CHAT_PEER


@pytest.mark.asyncio
async def test_use_not_registered(conn):
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_use(m, conn, "2099999999")
    assert "не зарегистрирована" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_use_wrong_format(conn):
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_use(m, conn, "abc")
    assert "Формат" in m.answers[-1]


# ============================================================
# /groups
# ============================================================


@pytest.mark.asyncio
async def test_groups_empty(admin_ids, conn):
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_groups(m, conn)
    assert "нет" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_groups_multiple(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    db.add_group(conn, CHAT_PEER + 1, "grp-301", "301", "СП-2")
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_groups(m, conn)
    text = m.answers[-1]
    assert "419" in text
    assert "301" in text
    assert "Группы (2)" in text


@pytest.mark.asyncio
async def test_groups_marks_active_session(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    db.add_group(conn, CHAT_PEER + 1, "grp-301", "301", "СП-2")
    db.set_user_session(conn, 123, CHAT_PEER)
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_groups(m, conn)
    assert "👈" in m.answers[-1]


@pytest.mark.asyncio
async def test_groups_denied_for_non_admin(admin_ids, conn):
    m = FakeMessage(peer_id=PM_PEER, from_id=999)
    await handlers.cmd_groups(m, conn)
    assert "нет прав" in m.answers[-1].lower()


# ============================================================
# /set_curator
# ============================================================


@pytest.mark.asyncio
async def test_set_curator_numeric_id(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_set_curator(m, conn, "555")
    assert db.get_group(conn, CHAT_PEER)["curator_id"] == 555


@pytest.mark.asyncio
async def test_set_curator_username(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")

    class FakeUser:
        id = 777
        first_name = "Юнева"
        last_name = "Т.А."

    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    m.ctx_api.users.get = AsyncMock(return_value=[FakeUser()])
    await handlers.cmd_set_curator(m, conn, "@юнева")
    assert db.get_group(conn, CHAT_PEER)["curator_id"] == 777


@pytest.mark.asyncio
async def test_set_curator_no_args(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_set_curator(m, conn, "")
    assert "Формат" in m.answers[-1]


@pytest.mark.asyncio
async def test_set_curator_group_not_registered(admin_ids, conn):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_set_curator(m, conn, "555")
    assert "не зарегистрирована" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_set_curator_username_not_found(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    m.ctx_api.users.get = AsyncMock(return_value=[])
    await handlers.cmd_set_curator(m, conn, "@несуществующий")
    assert "не найден" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_set_curator_for_teacher_forbidden(admin_ids, conn):
    """Попытка назначить куратора преподавателю должна выдавать ошибку."""
    db.add_teacher(conn, CHAT_PEER, "uuid-yuneva", "Юнева Н.Д.")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)

    await handlers.cmd_set_curator(m, conn, "555")

    assert "у преподавателя нет куратора" in m.answers[-1].lower()
    # Убеждаемся, что в БД ничего не записалось (там таблица teachers, а не groups)
    assert db.get_group(conn, CHAT_PEER) is None


# ============================================================
# /curator
# ============================================================


@pytest.mark.asyncio
async def test_curator_not_set(conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_curator(m, conn)
    assert "не назначен" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_curator_set(conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    db.set_group_curator(conn, CHAT_PEER, 555)

    class FakeUser:
        id = 555
        first_name = "Юнева"
        last_name = "Т.А."

    m = FakeMessage(peer_id=CHAT_PEER)
    m.ctx_api.users.get = AsyncMock(return_value=[FakeUser()])
    await handlers.cmd_curator(m, conn)
    text = m.answers[-1]
    assert "555" in text
    assert "Юнева" in text


# ============================================================
# /today, /tomorrow
# ============================================================


@pytest.mark.asyncio
async def test_today_no_group(conn):
    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_today(m, conn)
    assert "не зарегистрирована" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_today_success(conn, patch_base, patch_send, monkeypatch):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    lessons = [
        {"number": 1, "subgroup": 0, "discipline": "Web", "teacher": "T", "auditoria": "43", "lesson_type": "Лекция"}
    ]
    monkeypatch.setattr(schedule_api, "get_group_lessons", lambda b, g, d: lessons)

    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_today(m, conn)
    assert len(patch_send) == 1
    assert patch_send[0]["peer_id"] == CHAT_PEER
    assert patch_send[0]["msg"].kind == "day_image"


@pytest.mark.asyncio
async def test_today_api_error(conn, patch_base, monkeypatch):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")

    def boom(*a, **kw):
        raise schedule_api.ScheduleAPIError("down")

    monkeypatch.setattr(schedule_api, "get_group_lessons", boom)
    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_today(m, conn)
    assert "не удалось" in m.answers[-1].lower()


# ============================================================
# /week, /nextweek
# ============================================================


@pytest.mark.asyncio
async def test_week_current(conn, patch_base, patch_send, monkeypatch):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    captured = {}

    def fake_get_week(base, g, d):
        captured["monday"] = d
        return {d + timedelta(days=i): [] for i in range(7)}

    monkeypatch.setattr(schedule_api, "get_week_lessons", fake_get_week)
    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_week(m, conn)

    today = date.today()
    expected_monday = today - timedelta(days=today.weekday())
    assert captured["monday"] == expected_monday
    assert len(patch_send) == 1
    assert patch_send[0]["msg"].kind == "week_image"


@pytest.mark.asyncio
async def test_nextweek(conn, patch_base, patch_send, monkeypatch):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    captured = {}

    def fake_get_week(base, g, d):
        captured["monday"] = d
        return {d + timedelta(days=i): [] for i in range(7)}

    monkeypatch.setattr(schedule_api, "get_week_lessons", fake_get_week)
    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_nextweek(m, conn)

    today = date.today()
    expected_monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    assert captured["monday"] == expected_monday


@pytest.mark.asyncio
async def test_week_no_group(conn):
    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_week(m, conn)
    assert "не зарегистрирована" in m.answers[-1].lower()


# ============================================================
# /status
# ============================================================


@pytest.mark.asyncio
async def test_status_empty(admin_ids, conn):
    """Группа не определена — статус всё равно должен прийти."""
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_status(m, conn)
    # answers может содержать 2 сообщения: от _resolve + сам статус
    text_all = "\n".join(m.answers).lower()
    assert "не зарегистрирована" in text_all or "не определена" in text_all


@pytest.mark.asyncio
async def test_status_full(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    db.save_base_info(conn, {"x": 1})
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_status(m, conn)
    text = m.answers[-1]
    assert "419" in text
    assert "СП-4" in text


@pytest.mark.asyncio
async def test_status_denied_for_non_admin(admin_ids, conn):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=999)
    await handlers.cmd_status(m, conn)
    assert "нет прав" in m.answers[-1].lower()


# ============================================================
# /unsubscribe
# ============================================================


@pytest.mark.asyncio
async def test_unsubscribe(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_unsubscribe(m, conn)
    assert db.get_group(conn, CHAT_PEER) is None
    assert "отписка" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_unsubscribe_not_registered(admin_ids, conn):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_unsubscribe(m, conn)
    assert "не зарегистрирована" in m.answers[-1].lower()


# ============================================================
# /week_skip
# ============================================================


@pytest.mark.asyncio
async def test_week_skip_no_group(admin_ids, conn):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123, text="/week_skip")
    await handlers.cmd_week_skip(m, conn)
    assert "не зарегистрирована" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_week_skip_default_next_week(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_week_skip(m, conn)
    assert "помечена" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_week_skip_explicit_date(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_week_skip(m, conn, "21.09.2026")
    row = db.get_week_announced(conn, "grp-419", date(2026, 9, 21))
    assert row is not None
    assert row["is_full"] is False


@pytest.mark.asyncio
async def test_week_skip_wrong_format(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_week_skip(m, conn, "сегодня")
    assert "Формат" in m.answers[-1] or "❌" in m.answers[-1]


@pytest.mark.asyncio
async def test_week_skip_in_pm_with_peer(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_week_skip(m, conn, f"peer={CHAT_PEER}")
    assert "помечена" in m.answers[-1].lower()


# ============================================================
# is_admin / _require_admin
# ============================================================


def test_is_admin_true(admin_ids):
    m = FakeMessage(from_id=123)
    assert handlers.is_admin(m) is True


def test_is_admin_false(admin_ids):
    m = FakeMessage(from_id=999)
    assert handlers.is_admin(m) is False


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


# ============================================================
# Админ-команды отказывают не-админам
# ============================================================


@pytest.mark.asyncio
async def test_group_denied_for_non_admin(admin_ids, conn, patch_base):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=999)
    await handlers.cmd_group(m, conn, "СП-4 419")
    assert "нет прав" in m.answers[-1].lower()
    assert db.get_group(conn, CHAT_PEER) is None


@pytest.mark.asyncio
async def test_interval_denied_for_non_admin(admin_ids, conn):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=999)
    await handlers.cmd_interval(m, conn, "15")
    assert "нет прав" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_unsubscribe_denied_for_non_admin(admin_ids, conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=999)
    await handlers.cmd_unsubscribe(m, conn)
    assert "нет прав" in m.answers[-1].lower()
    assert db.get_group(conn, CHAT_PEER) is not None


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


# ============================================================
# Публичные команды работают для всех
# ============================================================


@pytest.mark.asyncio
async def test_start_public(admin_ids, conn):
    m = FakeMessage(from_id=999)
    await handlers.cmd_start(m, conn)
    assert "Привет" in m.answers[-1]


@pytest.mark.asyncio
async def test_where_public(admin_ids, conn):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=999)
    await handlers.cmd_where(m, conn)
    # Не должно быть "нет прав"
    assert "нет прав" not in m.answers[-1].lower()


# ============================================================
# /whoami
# ============================================================


@pytest.mark.asyncio
async def test_whoami_admin(admin_ids, conn):
    m = FakeMessage(from_id=123, peer_id=CHAT_PEER)
    await handlers.cmd_whoami(m, conn)
    text = m.answers[-1]
    assert "123" in text
    assert str(CHAT_PEER) in text
    assert "✅ да" in text


@pytest.mark.asyncio
async def test_whoami_non_admin(admin_ids, conn):
    m = FakeMessage(from_id=999, peer_id=CHAT_PEER)
    await handlers.cmd_whoami(m, conn)
    text = m.answers[-1]
    assert "999" in text
    assert "❌ нет" in text


# ============================================================
# /teacher
# ============================================================


@pytest.mark.asyncio
async def test_teacher_in_chat(admin_ids, conn, patch_base, monkeypatch):
    monkeypatch.setattr(
        schedule_api, "find_teacher", lambda b, n: {"id": "uuid-yuneva", "name": "Юнева Н.Д.", "divisions": ["div-1"]}
    )
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_teacher(m, conn, "Юнева Н.Д.")

    assert "зарегистрирован" in m.answers[-1].lower()
    t = db.get_teacher(conn, CHAT_PEER)
    assert t is not None
    assert t["teacher_uuid"] == "uuid-yuneva"


@pytest.mark.asyncio
async def test_teacher_in_pm_with_peer(admin_ids, conn, patch_base, monkeypatch):
    monkeypatch.setattr(
        schedule_api, "find_teacher", lambda b, n: {"id": "uuid-yuneva", "name": "Юнева Н.Д.", "divisions": ["div-1"]}
    )
    m = FakeMessage(peer_id=PM_PEER, from_id=123)
    await handlers.cmd_teacher(m, conn, f"peer={CHAT_PEER} Юнева Н.Д.")

    assert "зарегистрирован" in m.answers[-1].lower()
    assert db.get_teacher(conn, CHAT_PEER) is not None


@pytest.mark.asyncio
async def test_teacher_not_found(admin_ids, conn, patch_base, monkeypatch):
    monkeypatch.setattr(schedule_api, "find_teacher", lambda b, n: None)
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_teacher(m, conn, "Неизвестный П.П.")

    assert "не найден" in m.answers[-1].lower()
    assert db.get_teacher(conn, CHAT_PEER) is None


@pytest.mark.asyncio
async def test_teacher_empty_divisions(admin_ids, conn, patch_base, monkeypatch):
    monkeypatch.setattr(schedule_api, "find_teacher", lambda b, n: {"id": "uuid-x", "name": "Без Пар", "divisions": []})
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_teacher(m, conn, "Без Пар")

    assert "нет привязанных подразделений" in m.answers[-1].lower()
    assert db.get_teacher(conn, CHAT_PEER) is None


@pytest.mark.asyncio
async def test_teacher_denied_for_non_admin(admin_ids, conn):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=999)
    await handlers.cmd_teacher(m, conn, "Юнева Н.Д.")
    assert "нет прав" in m.answers[-1].lower()


# ============================================================
# Обновлённые команды (сущности)
# ============================================================


@pytest.mark.asyncio
async def test_where_teacher(conn):
    db.add_teacher(conn, CHAT_PEER, "uuid-1", "Юнева Наталья Дмитриевна")
    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_where(m, conn)
    assert "Преподаватель: Юнева Наталья Дмитриевна" in m.answers[-1]


@pytest.mark.asyncio
async def test_status_teacher(admin_ids, conn):
    db.add_teacher(conn, CHAT_PEER, "uuid-1", "Юнева Н.Д.")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_status(m, conn)
    assert "Преподаватель: Юнева Н.Д." in m.answers[-1]


@pytest.mark.asyncio
async def test_unsubscribe_teacher(admin_ids, conn):
    db.add_teacher(conn, CHAT_PEER, "uuid-1", "Юнева Н.Д.")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_unsubscribe(m, conn)
    assert db.get_teacher(conn, CHAT_PEER) is None
    assert "/teacher" in m.answers[-1]


@pytest.mark.asyncio
async def test_test_command_teacher(admin_ids, conn, patch_base, patch_send, monkeypatch):
    db.add_teacher(conn, CHAT_PEER, "uuid-1", "Юнева Н.Д.")
    monkeypatch.setattr(schedule_api, "get_teacher_lessons", lambda b, g, d: [])

    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_test(m, conn)

    assert any("Подписка: Юнева Н.Д." in a for a in m.answers)
    # Проверяем, что _send_day тоже отработал (вызвал send_schedule_message)
    assert len(patch_send) == 1


# ============================================================
# Команды контактов
# ============================================================


@pytest.mark.asyncio
async def test_add_contact_success(admin_ids, conn, patch_base, monkeypatch):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    monkeypatch.setattr(schedule_api, "find_teacher", lambda b, n: {"id": "uuid-1", "name": "Иванов Иван"})

    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_add_contact(m, conn, "Иванов Иван Почта: test@mail.ru Телефон: 123")

    assert "сохранены" in m.answers[-1].lower()
    c = db.get_contact(conn, CHAT_PEER, "uuid-1")
    assert c is not None
    assert c["emails"] == ["test@mail.ru"]
    assert c["phones"] == ["123"]


@pytest.mark.asyncio
async def test_add_contact_teacher_not_found(admin_ids, conn, patch_base, monkeypatch):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    monkeypatch.setattr(schedule_api, "find_teacher", lambda b, n: None)

    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_add_contact(m, conn, "Неизвестный Почта: a@b.c")

    assert "не найден в базе" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_add_contact_denied_for_non_admin(admin_ids, conn):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=999)
    await handlers.cmd_add_contact(m, conn, "Иванов Почта: a@b.c")
    assert "нет прав" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_update_contact_success(admin_ids, conn, patch_base, monkeypatch):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    monkeypatch.setattr(schedule_api, "find_teacher", lambda b, n: {"id": "uuid-1", "name": "Иванов Иван"})
    db.save_contact(conn, CHAT_PEER, "uuid-1", "Иванов Иван", emails=["old@b.c"])

    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_update_contact(m, conn, "Иванов Иван Телефон: 999")

    assert "обновлены поля" in m.answers[-1].lower()
    c = db.get_contact(conn, CHAT_PEER, "uuid-1")
    assert c["emails"] == ["old@b.c"]  # не стерлось
    assert c["phones"] == ["999"]  # добавилось


@pytest.mark.asyncio
async def test_update_contact_no_contact(admin_ids, conn, patch_base, monkeypatch):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    monkeypatch.setattr(schedule_api, "find_teacher", lambda b, n: {"id": "uuid-1", "name": "Иванов Иван"})

    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_update_contact(m, conn, "Иванов Иван Телефон: 999")

    assert "нет сохраненных контактов" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_remove_contact_success(admin_ids, conn, patch_base, monkeypatch):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    monkeypatch.setattr(schedule_api, "find_teacher", lambda b, n: {"id": "uuid-1", "name": "Иванов Иван"})
    db.save_contact(conn, CHAT_PEER, "uuid-1", "Иванов Иван")

    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_remove_contact(m, conn, "Иванов Иван")

    assert "удалены" in m.answers[-1].lower()
    assert db.get_contact(conn, CHAT_PEER, "uuid-1") is None


@pytest.mark.asyncio
async def test_contact_found(conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    db.save_contact(conn, CHAT_PEER, "uuid-1", "Иванов Иван", emails=["a@b.c"], phones=["123"])

    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_contact(m, conn, "Иванов")

    text = m.answers[-1]
    assert "Иванов Иван" in text
    assert "a@b.c" in text
    assert "123" in text


@pytest.mark.asyncio
async def test_contact_not_found(conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_contact(m, conn, "Неизвестный")
    assert "не найдены" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_contacts_list(conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    db.save_contact(conn, CHAT_PEER, "uuid-1", "Иванов Иван", telegram="@ivan")
    db.save_contact(conn, CHAT_PEER, "uuid-2", "Петров Петр", vk="vk.com/petrov")

    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_contacts(m, conn)

    # Убеждаемся, что выводятся оба (split_message может разбить на части)
    full_text = "\n".join(m.answers)
    assert "Иванов Иван" in full_text
    assert "Петров Петр" in full_text
    assert "@ivan" in full_text
    assert "vk.com/petrov" in full_text


# ============================================================
# Команды контактов студентов
# ============================================================


@pytest.mark.asyncio
async def test_add_student_contact_success(admin_ids, conn, patch_base):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_add_student_contact(
        m, conn, "Иванов Иван Телефон: 123 Почта: a@b.c Адрес: г. Сургут, ул. Ленина 1"
    )

    assert "сохранены" in m.answers[-1].lower()
    c = db.get_student_contact(conn, CHAT_PEER, "Иванов Иван")
    assert c is not None
    assert c["phones"] == ["123"]
    assert c["address"] == "г. Сургут, ул. Ленина 1"


@pytest.mark.asyncio
async def test_add_student_contact_denied_for_non_admin(admin_ids, conn):
    m = FakeMessage(peer_id=CHAT_PEER, from_id=999)
    await handlers.cmd_add_student_contact(m, conn, "Иванов Иван Телефон: 123")
    assert "нет прав" in m.answers[-1].lower()


@pytest.mark.asyncio
async def test_remove_student_contact_success(admin_ids, conn, patch_base):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    db.save_student_contact(conn, CHAT_PEER, "Иванов Иван")

    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_remove_student_contact(m, conn, "Иванов Иван")

    assert "удалены" in m.answers[-1].lower()
    assert db.get_student_contact(conn, CHAT_PEER, "Иванов Иван") is None


@pytest.mark.asyncio
async def test_student_contact_found(conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    db.save_student_contact(conn, CHAT_PEER, "Иванов Иван", phones=["123"], address="г. Сургут")

    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_student_contact(m, conn, "Иванов")

    text = m.answers[-1]
    assert "Иванов Иван" in text
    assert "123" in text
    assert "г. Сургут" in text


@pytest.mark.asyncio
async def test_students_contacts_list(conn):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    db.save_student_contact(conn, CHAT_PEER, "Иванов Иван", telegram="@ivan")
    db.save_student_contact(conn, CHAT_PEER, "Петров Петр", phones=["999"])

    m = FakeMessage(peer_id=CHAT_PEER)
    await handlers.cmd_students_contacts(m, conn)

    full_text = "\n".join(m.answers)
    assert "Иванов Иван" in full_text
    assert "Петров Петр" in full_text
    assert "@ivan" in full_text
    assert "999" in full_text


@pytest.mark.asyncio
async def test_update_student_contact_success(admin_ids, conn, patch_base):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    db.save_student_contact(conn, CHAT_PEER, "Иванов Иван", emails=["old@b.c"])

    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_update_student_contact(m, conn, "Иванов Иван Телефон: 999")

    assert "обновлены поля" in m.answers[-1].lower()
    c = db.get_student_contact(conn, CHAT_PEER, "Иванов Иван")
    assert c["emails"] == ["old@b.c"]  # не стерлось
    assert c["phones"] == ["999"]  # добавилось


@pytest.mark.asyncio
async def test_update_student_contact_no_contact(admin_ids, conn, patch_base):
    db.add_group(conn, CHAT_PEER, "grp-419", "419", "СП-4")
    m = FakeMessage(peer_id=CHAT_PEER, from_id=123)
    await handlers.cmd_update_student_contact(m, conn, "Иванов Иван Телефон: 999")
    assert "нет сохраненных контактов" in m.answers[-1].lower()
