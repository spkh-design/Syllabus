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


def _lesson(number=1, subgroup=0, discipline="Web", teacher="Дубров",
            auditoria="43", lesson_type="Лекция", territory=""):
    return {
        "number": number, "subgroup": subgroup, "discipline": discipline,
        "teacher": teacher, "auditoria": auditoria,
        "lesson_type": lesson_type, "territory": territory,
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
    # Вторник 8 сентября, 8:45 — 1-я пара ещё не началась (или идёт)
    scheduler.run_check_cycle(conn, base_info, "grp-419",
                              now=datetime(2026, 9, 8, 8, 45))

    # Меняем
    changed = [_lesson(1, teacher="Петров")]
    monkeypatch.setattr(
        schedule_api, "get_group_lessons", lambda b, g, d: changed,
    )
    # Тот же день, 9:00 — 1-я пара идёт (08:30–09:50)
    msgs1 = scheduler.run_check_cycle(conn, base_info, "grp-419",
                                       now=datetime(2026, 9, 8, 9, 0))
    assert len(msgs1) == 1

    # Ещё раз то же самое, 9:10 — не повторяем
    msgs2 = scheduler.run_check_cycle(conn, base_info, "grp-419",
                                       now=datetime(2026, 9, 8, 9, 10))
    assert msgs2 == []


def test_e2e_single_change_diff(conn, base_info, monkeypatch):
    """точечное изменение — одна замена препода → diff."""
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)

    initial = [_lesson(1, teacher="Дубров"), _lesson(2, teacher="Дубров")]
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: initial)
    # Первый цикл — снимок
    scheduler.run_check_cycle(conn, base_info, "grp-419",
                              now=datetime(2026, 9, 8, 8, 45))

    # Меняем только 1-ю пару
    changed = [_lesson(1, teacher="Петров"), _lesson(2, teacher="Дубров")]
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: changed)

    msgs = scheduler.run_check_cycle(conn, base_info, "grp-419",
                                      now=datetime(2026, 9, 8, 9, 0))
    assert len(msgs) == 1
    text = msgs[0]
    assert "ИЗМЕНЕНИЯ" in text
    assert "Петров" in text and "Дубров" in text
    # Это diff, а не full — нет строки "Всего изменений"
    assert "Всего изменений" not in text
    assert "Всего: добавлено" in text


def test_e2e_massive_change_full(conn, base_info, monkeypatch):
    """все пары дня стали Дистант → full."""
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)

    initial = [_lesson(i, auditoria="43") for i in range(1, 6)]
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: initial)
    scheduler.run_check_cycle(conn, base_info, "grp-419",
                              now=datetime(2026, 9, 8, 8, 45))

    changed = [_lesson(i, auditoria="Дистант") for i in range(1, 6)]
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: changed)

    msgs = scheduler.run_check_cycle(conn, base_info, "grp-419",
                                      now=datetime(2026, 9, 8, 9, 0))
    assert len(msgs) == 1
    text = msgs[0]
    assert "Всего изменений" in text
    assert "Дистант" in text


def test_e2e_changes_after_last_lesson_silent(conn, base_info, monkeypatch):
    """изменения на сегодня после последней пары — молчим."""
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)

    initial = [_lesson(1, teacher="Дубров")]
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: initial)
    scheduler.run_check_cycle(conn, base_info, "grp-419",
                              now=datetime(2026, 9, 8, 8, 30))

    changed = [_lesson(1, teacher="Петров")]
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: changed)

    # 10:00 — 1-я пара вторника 08:30–09:50 уже прошла
    msgs = scheduler.run_check_cycle(conn, base_info, "grp-419",
                                      now=datetime(2026, 9, 8, 10, 0))
    assert msgs == []

    # Снимок при этом обновлён (сохранён новый)
    snap = db.get_snapshot(conn, "grp-419", date(2026, 9, 8))
    assert snap["lessons"][0]["teacher"] == "Петров"


def test_e2e_full_week_announced(conn, base_info, monkeypatch):
    """полная неделя в Пт/Сб/Вс → автообъявление."""
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)

    def fake_week(base, g, start):
        # 7 дней с парами — «полная неделя»
        return {start + timedelta(days=i): [_lesson(1)] for i in range(7)}

    monkeypatch.setattr(schedule_api, "get_week_lessons", fake_week)
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: [_lesson(1)])

    # Вс 13.09 15:00 — окно следующей недели
    now = datetime(2026, 9, 13, 15, 0)
    msgs = scheduler.run_check_cycle(conn, base_info, "grp-419", now=now)

    # Одно сообщение — расписание недели
    assert len(msgs) >= 1
    joined = "\n".join(msgs)
    assert "Расписание на неделю" in joined
    # Флаг поставлен
    row = db.get_week_announced(conn, "grp-419", date(2026, 9, 14))
    assert row is not None
    assert row["is_full"] is True


def test_e2e_b2_partial_week_sunday_after_13(conn, base_info, monkeypatch):
    """B2: неполная неделя + воскресенье 13:00+ → отправляется."""
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)

    def fake_week(base, g, start):
        # Только 2 дня с парами — неполная неделя
        week = {start + timedelta(days=i): [] for i in range(7)}
        week[start] = [_lesson(1)]
        week[start + timedelta(days=2)] = [_lesson(1)]
        return week

    monkeypatch.setattr(schedule_api, "get_week_lessons", fake_week)
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: [])

    # Вс 13.09 14:00 — после 13:00
    now = datetime(2026, 9, 13, 14, 0)
    msgs = scheduler.run_check_cycle(conn, base_info, "grp-419", now=now)

    joined = "\n".join(msgs)
    assert "Расписание на неделю" in joined
    # Флаг is_full=False
    row = db.get_week_announced(conn, "grp-419", date(2026, 9, 14))
    assert row is not None
    assert row["is_full"] is False


def test_e2e_partial_week_sunday_before_13(conn, base_info, monkeypatch):
    """неполная неделя + воскресенье ДО 13:00 → тишина."""
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)

    def fake_week(base, g, start):
        week = {start + timedelta(days=i): [] for i in range(7)}
        week[start] = [_lesson(1)]
        return week

    monkeypatch.setattr(schedule_api, "get_week_lessons", fake_week)
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: [])

    # Вс 13.09 12:00 — до 13:00
    now = datetime(2026, 9, 13, 12, 0)
    msgs = scheduler.run_check_cycle(conn, base_info, "grp-419", now=now)

    joined = "\n".join(msgs)
    assert "Расписание на неделю" not in joined
    assert db.get_week_announced(conn, "grp-419", date(2026, 9, 14)) is None


def test_e2e_week_skip_silences_announce(conn, base_info, monkeypatch):
    """/week_skip → автообъявление не срабатывает."""
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)
    # Заранее ставим флаг
    db.set_week_announced(conn, "grp-419", date(2026, 9, 14), is_full=False)

    def fake_week(base, g, start):
        return {start + timedelta(days=i): [_lesson(1)] for i in range(7)}

    monkeypatch.setattr(schedule_api, "get_week_lessons", fake_week)
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: [_lesson(1)])

    now = datetime(2026, 9, 13, 15, 0)
    msgs = scheduler.run_check_cycle(conn, base_info, "grp-419", now=now)

    joined = "\n".join(msgs)
    assert "Расписание на неделю" not in joined


def test_e2e_first_week_of_semester_no_announce(conn, base_info, monkeypatch):
    """первая неделя семестра (1–7.09) → автообъявления нет."""
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)

    def fake_week(base, g, start):
        return {start + timedelta(days=i): [_lesson(1)] for i in range(7)}

    monkeypatch.setattr(schedule_api, "get_week_lessons", fake_week)
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: [_lesson(1)])

    # 4 сентября (первая неделя) — Пт? Нет, 4.09.2026 — пятница. Ок.
    # Но should_check_now для Пт вернёт следующую неделю только с 14:00.
    # Нужно попасть в окно и в первую неделю. 4 сентября 15:00 — да.
    now = datetime(2026, 9, 4, 15, 0)
    msgs = scheduler.run_check_cycle(conn, base_info, "grp-419", now=now)

    # Автообъявления нет
    joined = "\n".join(msgs)
    assert "Расписание на неделю" not in joined
    assert db.get_week_announced(conn, "grp-419", date(2026, 9, 7)) is None


def test_e2e_three_days_sends_whole_week(conn, base_info, monkeypatch):
    """3+ дня с изменениями → одно большое сообщение со всей неделей."""
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)
    # Заранее ставим флаг, чтобы не было автообъявления
    db.set_week_announced(conn, "grp-419", date(2026, 9, 14), is_full=True)

    # 3 дня с изменениями
    days_changed = [date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16)]
    for d in days_changed:
        db.save_snapshot(conn, "grp-419", d, [_lesson(1, teacher="Иванов")])

    def fake_get(base, g, d):
        if d in days_changed:
            return [_lesson(1, teacher="Петров")]
        return []

    monkeypatch.setattr(schedule_api, "get_group_lessons", fake_get)

    now = datetime(2026, 9, 13, 15, 0)   # Вс
    msgs = scheduler.run_check_cycle(conn, base_info, "grp-419", now=now)

    joined = "\n".join(msgs)
    # Большое недельное сообщение
    assert "⚠️ ИЗМЕНЕНИЯ:" in joined
    assert "Всего изменений за неделю" in joined
    assert "🔔" in joined
    # Дневных "Всего: добавлено" быть не должно
    assert "Всего: добавлено" not in joined


def test_e2e_two_days_sends_individual(conn, base_info, monkeypatch):
    """2 дня с изменениями → два отдельных сообщения."""
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)
    db.set_week_announced(conn, "grp-419", date(2026, 9, 14), is_full=True)

    days_changed = [date(2026, 9, 14), date(2026, 9, 15)]
    for d in days_changed:
        db.save_snapshot(conn, "grp-419", d, [_lesson(1, teacher="Иванов")])

    def fake_get(base, g, d):
        if d in days_changed:
            return [_lesson(1, teacher="Петров")]
        return []

    monkeypatch.setattr(schedule_api, "get_group_lessons", fake_get)

    now = datetime(2026, 9, 13, 15, 0)
    msgs = scheduler.run_check_cycle(conn, base_info, "grp-419", now=now)

    # Два отдельных diff-сообщения
    assert len(msgs) == 2
    for m in msgs:
        assert "Всего: добавлено" in m
        assert "Всего изменений за неделю" not in m


def test_e2e_territory_in_diff(conn, base_info, monkeypatch):
    """пара в чужом СП — в diff видно «СП-5»."""
    db.set_setting(conn, "group_uuid", "grp-419")
    db.set_setting(conn, "peer_id", "12345")
    db.save_base_info(conn, base_info)

    initial = [_lesson(1, auditoria="43", territory="(СП-4) Энергетическое")]
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: initial)
    scheduler.run_check_cycle(conn, base_info, "grp-419",
                              now=datetime(2026, 9, 8, 8, 45))

    # Пара переехала в СП-5
    changed = [_lesson(1, auditoria="12", territory="(СП-5) МФЦПК")]
    monkeypatch.setattr(schedule_api, "get_group_lessons",
                        lambda b, g, d: changed)

    msgs = scheduler.run_check_cycle(conn, base_info, "grp-419",
                                      now=datetime(2026, 9, 8, 9, 0))
    assert len(msgs) == 1
    assert "СП-5" in msgs[0]


def test_e2e_no_admins_denies(monkeypatch):
    """пустой ADMIN_USER_IDS → никто не админ."""
    import config
    monkeypatch.setattr(config, "ADMIN_USER_IDS", [])
    assert config.is_admin_id(123) is False
    assert config.is_admin_id(0) is False
    assert config.is_admin_id(999) is False