"""Тесты для scheduler.py.

Планировщик не ходит в интернет — HTTP замокан.
БД — временный файл через tmp_path.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

import database as db
import scheduler
import schedule_api


# ---------- Фикстуры ----------

@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "sched.db")
    yield c
    c.close()


@pytest.fixture
def base_info():
    return {
        "divisions": [
            {"name": "(СП-4) Энергетическое отделени", "id": "div-sp4"},
        ],
        "groups": [
            {"name": "419", "id": "grp-419", "division": "div-sp4", "curse": 3},
        ],
        "teachers": [{"name": "Дубров Никита Александрович", "id": "t-dubrov"}],
        "disciplines": [{"name": "Web", "id": "d-web"}],
        "lesson_Types": [{"name": "Лекция", "id": "lt-lec"}],
        "audithories": [{"name": "Каб. 43", "short_name": "43", "id": "a-43"}],
        "territories": [],
    }


def _lesson(number=1, subgroup=0, discipline="Web", teacher="Дубров Никита Александрович",
            auditoria="43", lesson_type="Лекция", territory="",):
    return {
        "number": number, "subgroup": subgroup, "discipline": discipline,
        "teacher": teacher, "auditoria": auditoria, "lesson_type": lesson_type, "territory": territory,
    }


# ---------- should_check_now: Пн–Пт ----------

def test_check_today_weekday_morning():
    """Пн 07:00 — только сегодня."""
    now = datetime(2026, 9, 7, 7, 0)  # Пн
    assert scheduler.should_check_now(now) == {date(2026, 9, 7)}


def test_check_today_weekday_midday():
    """Вт 12:00 — только сегодня."""
    now = datetime(2026, 9, 8, 12, 0)
    assert scheduler.should_check_now(now) == {date(2026, 9, 8)}


def test_check_today_and_tomorrow_afternoon():
    """Вт 15:00 — сегодня и завтра."""
    now = datetime(2026, 9, 8, 15, 0)
    assert scheduler.should_check_now(now) == {date(2026, 9, 8), date(2026, 9, 9)}


def test_check_weekday_night():
    """Вт 23:30 — ничего не проверяем."""
    now = datetime(2026, 9, 8, 23, 30)
    assert scheduler.should_check_now(now) == set()


def test_check_weekday_before_window():
    """Вт 06:00 — ещё не время."""
    now = datetime(2026, 9, 8, 6, 0)
    assert scheduler.should_check_now(now) == set()


# ---------- should_check_now: Пт ----------

def test_check_friday_afternoon_includes_next_week():
    """Пт 15:00 — сегодня, завтра и вся следующая неделя."""
    now = datetime(2026, 9, 11, 15, 0)  # Пт
    dates = scheduler.should_check_now(now)
    # Сегодня
    assert date(2026, 9, 11) in dates
    # Завтра
    assert date(2026, 9, 12) in dates
    # Следующая неделя: Пн 14.09 — Вс 20.09
    for i in range(7):
        assert date(2026, 9, 14) + timedelta(days=i) in dates
    # Текущая неделя (Сб 12.09 уже попала как «завтра», но Вс 13.09 не должна)
    assert date(2026, 9, 13) not in dates


def test_check_friday_morning_no_next_week():
    """Пт 10:00 — только сегодня, без завтра и след. недели."""
    now = datetime(2026, 9, 11, 10, 0)
    dates = scheduler.should_check_now(now)
    assert dates == {date(2026, 9, 11)}


# ---------- should_check_now: Сб ----------

def test_check_saturday_morning_next_week_only():
    """Сб 09:00 — вся следующая неделя."""
    now = datetime(2026, 9, 12, 9, 0)
    dates = scheduler.should_check_now(now)
    assert len(dates) == 7
    for i in range(7):
        assert date(2026, 9, 14) + timedelta(days=i) in dates
    # Сегодня и завтра — не проверяем
    assert date(2026, 9, 12) not in dates
    assert date(2026, 9, 13) not in dates


def test_check_saturday_before_window():
    """Сб 07:00 — ничего."""
    now = datetime(2026, 9, 12, 7, 0)
    assert scheduler.should_check_now(now) == set()


# ---------- should_check_now: Вс ----------

def test_check_sunday_morning_next_week():
    """Вс 09:00 — вся следующая неделя."""
    now = datetime(2026, 9, 13, 9, 0)
    dates = scheduler.should_check_now(now)
    assert len(dates) == 7
    for i in range(7):
        assert date(2026, 9, 14) + timedelta(days=i) in dates


def test_check_sunday_afternoon_adds_tomorrow():
    """Вс 15:00 — следующая неделя + завтра (=Пн 14.09, уже в неделе)."""
    now = datetime(2026, 9, 13, 15, 0)
    dates = scheduler.should_check_now(now)
    # Пн 14.09 уже входит в next_week, дублирования не должно быть
    assert len(dates) == 7
    assert date(2026, 9, 14) in dates


def test_check_sunday_late_evening_still_week():
    """Вс 22:30 — всё ещё вся следующая неделя."""
    now = datetime(2026, 9, 13, 22, 30)
    dates = scheduler.should_check_now(now)
    assert len(dates) == 7


# ---------- check_group ----------

def test_check_group_first_run_saves_no_message(conn, base_info, monkeypatch):
    """Первый запуск — снимка нет, уведомление не отправляется."""
    monkeypatch.setattr(
        schedule_api, "get_group_lessons",
        lambda b, g, d: [_lesson(1)],
    )

    result = scheduler.check_group(conn, base_info, "grp-419", date(2026, 9, 8))
    assert result is None
    # Снимок сохранён
    snap = db.get_snapshot(conn, "grp-419", date(2026, 9, 8))
    assert snap is not None


def test_check_group_no_changes(conn, base_info, monkeypatch):
    """Снимок есть, ничего не изменилось — None."""
    lessons = [_lesson(1)]
    db.save_snapshot(conn, "grp-419", date(2026, 9, 8), lessons)

    monkeypatch.setattr(
        schedule_api, "get_group_lessons",
        lambda b, g, d: lessons,
    )
    result = scheduler.check_group(conn, base_info, "grp-419", date(2026, 9, 8))
    assert result is None


def test_check_group_single_change_diff(conn, base_info, monkeypatch):
    """Изменился преподаватель одной пары — short diff."""
    old = [_lesson(1, teacher="Иванов")]
    new = [_lesson(1, teacher="Петров")]
    db.save_snapshot(conn, "grp-419", date(2026, 9, 8), old)

    monkeypatch.setattr(
        schedule_api, "get_group_lessons",
        lambda b, g, d: new,
    )
    result = scheduler.check_group(conn, base_info, "grp-419", date(2026, 9, 8))
    assert result is not None
    kind, text = result
    assert kind == "diff"
    assert "Иванов" in text and "Петров" in text


def test_check_group_massive_change_full(conn, base_info, monkeypatch):
    """5 пар из 5 стали Дистант — full."""
    old = [_lesson(i, auditoria="43") for i in range(1, 6)]
    new = [_lesson(i, auditoria="Дистант") for i in range(1, 6)]
    db.save_snapshot(conn, "grp-419", date(2026, 9, 8), old)

    monkeypatch.setattr(
        schedule_api, "get_group_lessons",
        lambda b, g, d: new,
    )
    result = scheduler.check_group(conn, base_info, "grp-419", date(2026, 9, 8))
    assert result is not None
    kind, text = result
    assert kind == "full"
    assert "Дистант" in text
    assert "Всего изменений" in text


def test_check_group_api_error_returns_none(conn, base_info, monkeypatch):
    """API упал — не падаем, возвращаем None."""
    def boom(*a, **kw):
        raise schedule_api.ScheduleAPIError("network down")

    monkeypatch.setattr(schedule_api, "get_group_lessons", boom)
    result = scheduler.check_group(conn, base_info, "grp-419", date(2026, 9, 8))
    assert result is None


def test_check_group_logs_notification(conn, base_info, monkeypatch):
    """При изменении пишем в notifications_log."""
    old = [_lesson(1, teacher="Иванов")]
    new = [_lesson(1, teacher="Петров")]
    db.save_snapshot(conn, "grp-419", date(2026, 9, 8), old)

    monkeypatch.setattr(
        schedule_api, "get_group_lessons",
        lambda b, g, d: new,
    )
    scheduler.check_group(conn, base_info, "grp-419", date(2026, 9, 8))
    last = db.last_notification(conn, "grp-419", date(2026, 9, 8))
    assert last is not None
    assert last["kind"] == "diff"


def test_check_group_updates_snapshot_before_sending(conn, base_info, monkeypatch):
    """Снимок обновляется ДО отправки — если отправка упадёт, не повторяем."""
    old = [_lesson(1, teacher="Иванов")]
    new = [_lesson(1, teacher="Петров")]
    db.save_snapshot(conn, "grp-419", date(2026, 9, 8), old)

    monkeypatch.setattr(
        schedule_api, "get_group_lessons",
        lambda b, g, d: new,
    )
    scheduler.check_group(conn, base_info, "grp-419", date(2026, 9, 8))

    snap = db.get_snapshot(conn, "grp-419", date(2026, 9, 8))
    assert snap["lessons"][0]["teacher"] == "Петров"


# ---------- run_check_cycle ----------

def test_run_cycle_no_dates(conn, base_info, monkeypatch):
    """Пт 23:30 — ничего не проверяем."""
    now = datetime(2026, 9, 11, 23, 30)
    monkeypatch.setattr(
        schedule_api, "get_group_lessons",
        lambda b, g, d: [],
    )
    msgs = scheduler.run_check_cycle(conn, base_info, "grp-419", now=now)
    assert msgs == []


def test_run_cycle_with_changes(conn, base_info, monkeypatch):
    """Вс 15:00 — проверяем неделю, где-то есть изменение."""
    # Сначала заполним снимки для всей недели
    for i in range(7):
        d = date(2026, 9, 14) + timedelta(days=i)
        db.save_snapshot(conn, "grp-419", d, [_lesson(1, teacher="Иванов")])

    # На среду поставим другого преподавателя
    def fake_get(base, g, d):
        if d == date(2026, 9, 16):
            return [_lesson(1, teacher="Петров")]
        return [_lesson(1, teacher="Иванов")]

    monkeypatch.setattr(schedule_api, "get_group_lessons", fake_get)

    now = datetime(2026, 9, 13, 15, 0)  # Вс
    msgs = scheduler.run_check_cycle(conn, base_info, "grp-419", now=now)
    assert len(msgs) >= 1
    assert any("Петров" in m for m in msgs)


def test_run_cycle_no_base_returns_empty(conn, monkeypatch):
    """Нет справочников — цикл пропускается."""
    msgs = scheduler.run_check_cycle(conn, None, "grp-419",
                                     now=datetime(2026, 9, 8, 10, 0))
    assert msgs == []


# ---------- refresh_base_info_if_needed ----------

def test_refresh_base_info_loads_when_empty(conn, monkeypatch):
    fresh = {"divisions": [], "groups": []}
    monkeypatch.setattr(schedule_api, "fetch_base_info", lambda: fresh)
    result = scheduler.refresh_base_info_if_needed(conn, None)
    assert result == fresh


def test_refresh_base_info_skips_when_fresh(conn, monkeypatch):
    """Если справочники моложе 24 ч — API не дёргается."""
    db.save_base_info(conn, {"v": 1})
    called = []
    monkeypatch.setattr(schedule_api, "fetch_base_info",
                        lambda: called.append(1) or {"v": 2})
    result = scheduler.refresh_base_info_if_needed(conn, {"v": 1})
    assert called == []
    assert result == {"v": 1}


def test_refresh_base_info_handles_api_error(conn, monkeypatch):
    def boom():
        raise schedule_api.ScheduleAPIError("nope")
    monkeypatch.setattr(schedule_api, "fetch_base_info", boom)
    db.save_base_info(conn, {"v": 1})
    # Справочники есть в БД, но «старые» (age=None/большой)
    result = scheduler.refresh_base_info_if_needed(conn, None)
    assert result == {"v": 1}  # из БД


def test_metrics_increment_on_cycle(conn, base_info, monkeypatch):
    """После цикла метрики обновляются."""
    monkeypatch.setattr(
        schedule_api, "get_group_lessons",
        lambda b, g, d: [],
    )

    now = datetime(2026, 9, 8, 10, 0)  # Вт
    scheduler.run_check_cycle(conn, base_info, "grp-419", now=now)

    assert db.get_setting(conn, "metrics_checks") == 1
    assert db.get_setting(conn, "metrics_last_check") is not None


def test_metrics_increment_on_api_error(conn, base_info, monkeypatch):
    """При ошибке API счётчик ошибок растёт."""
    def boom(*a, **kw):
        raise schedule_api.ScheduleAPIError("down")
    monkeypatch.setattr(schedule_api, "get_group_lessons", boom)

    # Заполним снимок, чтобы не сработал «первый запуск»
    db.save_snapshot(conn, "grp-419", date(2026, 9, 8), [_lesson(1)])

    now = datetime(2026, 9, 8, 10, 0)
    scheduler.run_check_cycle(conn, base_info, "grp-419", now=now)

    assert db.get_setting(conn, "metrics_errors", 0) >= 1


def test_metrics_notifications_count(conn, base_info, monkeypatch):
    """Счётчик уведомлений растёт при изменении."""
    db.save_snapshot(conn, "grp-419", date(2026, 9, 8),
                     [_lesson(1, teacher="Иванов")])

    monkeypatch.setattr(
        schedule_api, "get_group_lessons",
        lambda b, g, d: [_lesson(1, teacher="Петров")],
    )
    now = datetime(2026, 9, 8, 10, 0)
    scheduler.run_check_cycle(conn, base_info, "grp-419", now=now)

    # 1 уведомление (diff) — 1 сообщение
    assert db.get_setting(conn, "metrics_notifications", 0) >= 1


def test_check_group_massive_with_territory(conn, base_info, monkeypatch):
    """Пара переехала в СП-5 — в full-сообщении видно «СП-5»."""
    old = [_lesson(1, auditoria="43", territory="(СП-4) Энергетическое")]
    new = [_lesson(1, auditoria="12", territory="(СП-5) МФЦПК")]
    db.save_snapshot(conn, "grp-419", date(2026, 9, 8), old)

    monkeypatch.setattr(
        schedule_api, "get_group_lessons", lambda b, g, d: new,
    )
    result = scheduler.check_group(conn, base_info, "grp-419", date(2026, 9, 8))
    assert result is not None
    kind, text = result
    assert "СП-5" in text