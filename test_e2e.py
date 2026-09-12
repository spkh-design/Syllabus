"""End-to-end тест: имитация полного жизненного цикла бота.

Проверяет связку: БД + scheduler + comparator + formatter.
HTTP замокан, ВК не задействован.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

import database as db
import scheduler
import schedule_api


def _lesson(number, teacher="Дубров", auditoria="43", lesson_type="Лекция",
            discipline="Web"):
    return {
        "number": number, "subgroup": 0, "discipline": discipline,
        "teacher": teacher, "auditoria": auditoria, "lesson_type": lesson_type,
    }


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "e2e.db")
    yield c
    c.close()


@pytest.fixture
def base_info():
    return {
        "divisions": [{"name": "(СП-2) Отделение", "id": "div-sp2"}],
        "groups": [{"name": "419", "id": "grp-419",
                    "division": "div-sp2", "curse": 3}],
        "teachers": [{"name": "Дубров", "id": "t1"}],
        "disciplines": [{"name": "Web", "id": "d1"}],
        "lesson_Types": [{"name": "Лекция", "id": "lt1"}],
        "audithories": [{"name": "Каб 43", "short_name": "43", "id": "a43"}],
        "territories": [],
    }


def test_e2e_full_cycle(conn, base_info, monkeypatch):
    """
    Сценарий:
      1. Пользователь настраивает группу.
      2. Первый цикл проверки — сохраняется снимок, уведомлений нет.
      3. Расписание меняется.
      4. Второй цикл — приходит diff-уведомление.
    """
    # --- Шаг 1: настройка группы ---
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "group_name", "419")
    db.set_setting(conn, "division", "СП-2")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)

    # --- Шаг 2: первый цикл, расписание "как есть" ---
    initial = [_lesson(1, teacher="Дубров"), _lesson(2, teacher="Дубров")]

    monkeypatch.setattr(
        schedule_api, "get_group_lessons",
        lambda b, g, d: initial,
    )

    # Вт 10:00 — проверяем только сегодня
    t1 = datetime(2026, 9, 8, 10, 0)
    messages = scheduler.run_check_cycle(conn, base_info, "grp-419", now=t1)
    assert messages == []  # первый запуск — без уведомлений
    assert db.get_snapshot(conn, "grp-419", date(2026, 9, 8)) is not None

    # --- Шаг 3: расписание меняется (одна замена преподавателя) ---
    changed = [_lesson(1, teacher="Петров"), _lesson(2, teacher="Дубров")]

    monkeypatch.setattr(
        schedule_api, "get_group_lessons",
        lambda b, g, d: changed,
    )

    # --- Шаг 4: второй цикл — приходит diff ---
    t2 = datetime(2026, 9, 8, 10, 30)
    messages = scheduler.run_check_cycle(conn, base_info, "grp-419", now=t2)

    assert len(messages) == 1
    text = messages[0]
    assert "⚠️ ИЗМЕНЕНИЯ" in text
    assert "Петров" in text
    assert "Дубров" in text
    assert "преподаватель" in text.lower() or "препод" in text.lower()


def test_e2e_massive_change_sends_full(conn, base_info, monkeypatch):
    """
    Сценарий массового изменения: на весь день поставили дистант.
    Ожидаем полное расписание дня.
    """
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)

    initial = [_lesson(i, auditoria="43") for i in range(1, 6)]

    monkeypatch.setattr(
        schedule_api, "get_group_lessons", lambda b, g, d: initial,
    )
    t1 = datetime(2026, 9, 8, 10, 0)
    scheduler.run_check_cycle(conn, base_info, "grp-419", now=t1)

    # Все пары стали дистанционными
    changed = [_lesson(i, auditoria="Дистант") for i in range(1, 6)]
    monkeypatch.setattr(
        schedule_api, "get_group_lessons", lambda b, g, d: changed,
    )
    t2 = datetime(2026, 9, 8, 10, 30)
    messages = scheduler.run_check_cycle(conn, base_info, "grp-419", now=t2)

    assert len(messages) == 1
    text = messages[0]
    assert "Дистант" in text
    assert "Всего изменений: 5" in text
    # Все 5 пар показаны в полном виде
    assert "1. 08:30" in text
    assert "5. 15:10" in text


def test_e2e_no_repeat_on_same_change(conn, base_info, monkeypatch):
    """Если снимок обновился, а расписание не менялось — повторно не шлём."""
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)

    initial = [_lesson(1, teacher="Дубров")]
    monkeypatch.setattr(
        schedule_api, "get_group_lessons", lambda b, g, d: initial,
    )
    scheduler.run_check_cycle(conn, base_info, "grp-419",
                              now=datetime(2026, 9, 8, 10, 0))

    # Меняем
    changed = [_lesson(1, teacher="Петров")]
    monkeypatch.setattr(
        schedule_api, "get_group_lessons", lambda b, g, d: changed,
    )
    msgs1 = scheduler.run_check_cycle(conn, base_info, "grp-419",
                                       now=datetime(2026, 9, 8, 10, 30))
    assert len(msgs1) == 1

    # Ещё раз то же самое — не повторяем
    msgs2 = scheduler.run_check_cycle(conn, base_info, "grp-419",
                                       now=datetime(2026, 9, 8, 11, 0))
    assert msgs2 == []