"""Тесты для formatter.py."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from comparator import Change
from formatter import (
    CHANGE_MARKER,
    VK_MESSAGE_LIMIT,
    format_changes,
    format_day_changes_full,
    format_day_schedule,
    format_week_changes_full,
    format_week_schedule,
    split_message,
)

# ---------- Хелперы ----------


def L(
    number,
    subgroup=0,
    discipline="Дисциплина",
    teacher="Преподаватель",
    auditoria="43",
    lesson_type="Лекция",
    territory="",
):
    return {
        "number": number,
        "subgroup": subgroup,
        "discipline": discipline,
        "teacher": teacher,
        "auditoria": auditoria,
        "lesson_type": lesson_type,
        "territory": territory,
    }


# ---------- format_day_schedule ----------


def test_day_schedule_empty():
    text = format_day_schedule(date(2026, 9, 12), [])
    assert "Пар нет" in text
    assert "12.09.2026" in text


def test_day_schedule_single_lesson():
    """Пара №1 вторника — должна быть 08:30–09:50."""
    d = date(2026, 9, 8)  # вторник
    lessons = [L(1, discipline="Web", teacher="Дубров", auditoria="43")]
    text = format_day_schedule(d, lessons)

    assert "08:30–09:50" in text
    assert "Web" in text
    assert "Дубров" in text
    assert "каб. 43" in text


def test_day_schedule_multiple_lessons():
    d = date(2026, 9, 8)  # вторник
    lessons = [L(1, discipline="A"), L(2, discipline="B")]
    text = format_day_schedule(d, lessons)
    assert "1. 08:30–09:50" in text
    assert "2. 10:00–11:20" in text
    assert "A" in text and "B" in text


def test_day_schedule_monday_shifted():
    """Пн — пары на 30 мин позже, чем Вт."""
    d = date(2026, 9, 7)  # понедельник
    lessons = [L(1)]
    text = format_day_schedule(d, lessons)
    assert "09:00–10:20" in text
    assert "08:30" not in text


def test_day_schedule_saturday_short():
    """Сб — пары по часу."""
    d = date(2026, 9, 12)  # суббота
    lessons = [L(1), L(2)]
    text = format_day_schedule(d, lessons)
    assert "08:30–09:30" in text
    assert "09:40–10:40" in text


def test_day_schedule_with_subgroup():
    d = date(2026, 9, 8)
    text = format_day_schedule(d, [L(1, subgroup=1)])
    assert "(подгр. 1)" in text


def test_day_schedule_no_subgroup_label_when_zero():
    d = date(2026, 9, 8)
    text = format_day_schedule(d, [L(1, subgroup=0)])
    assert "подгр." not in text


def test_day_schedule_includes_weekday_name():
    text = format_day_schedule(date(2026, 9, 8), [])
    assert "(Вт)" in text


# ---------- format_week_schedule ----------


def test_week_schedule_empty_days():
    """Пустые дни должны быть отмечены (Пн–Сб)."""
    start = date(2026, 9, 7)  # понедельник
    week = {start + timedelta(days=i): [] for i in range(7)}
    text = format_week_schedule(start, week)
    assert "07.09" in text
    assert "12.09" in text
    assert "13.09" not in text
    assert text.count("пар нет") == 6


def test_week_schedule_with_lessons():
    start = date(2026, 9, 7)
    week = {start: [L(1, discipline="Математика")], start + timedelta(days=1): [L(1, discipline="Русский")]}
    for i in range(2, 7):
        week[start + timedelta(days=i)] = []

    text = format_week_schedule(start, week)
    assert "Математика" in text
    assert "Русский" in text
    assert "07.09" in text
    assert "08.09" in text


def test_week_schedule_header_has_range():
    start = date(2026, 9, 7)
    week = {start + timedelta(days=i): [] for i in range(7)}
    text = format_week_schedule(start, week)
    assert "07.09.2026" in text
    assert "12.09.2026" in text
    assert "13.09.2026" not in text


# ---------- format_changes ----------


def test_format_changes_empty():
    text = format_changes(date(2026, 9, 8), [], [])
    assert text == ""


def test_format_changes_single_modify():
    c = Change("modify", 1, 0, L(1), {"teacher": ("Иванов", "Петров")})
    text = format_changes(date(2026, 9, 8), [c], [L(1)])
    assert "⚠️ ИЗМЕНЕНИЯ" in text
    assert "Пара 1" in text
    assert "Иванов" in text and "Петров" in text
    assert "изменено 1" in text


def test_format_changes_multiple():
    changes = [
        Change("add", 3, 0, L(3)),
        Change("remove", 4, 0, L(4)),
        Change("modify", 1, 0, L(1), {"auditoria": ("43", "45")}),
    ]
    text = format_changes(date(2026, 9, 8), changes, [L(1), L(3)])
    assert "добавлено 1" in text
    assert "отменено 1" in text
    assert "изменено 1" in text


def test_format_changes_no_group_in_header():
    """В шапке — только дата, без номера группы."""
    c = Change("modify", 1, 0, L(1), {"teacher": ("A", "B")})
    text = format_changes(date(2026, 9, 8), [c], [L(1)])
    # В шапке не должно быть слова "группа" или номера
    header = text.split("\n")[0]
    assert "группа" not in header.lower()
    assert "419" not in header


# ---------- format_day_changes_full ----------


def test_format_full_changes_empty_new():
    """Все пары отменили."""
    changes = [Change("remove", i, 0, L(i)) for i in range(1, 4)]
    text = format_day_changes_full(date(2026, 9, 8), changes, [])
    assert "ИЗМЕНЕНИЯ" in text
    assert "Пар нет" in text


def test_format_full_changes_with_lessons():
    """Показали 3 пары и итог."""
    changes = [Change("modify", i, 0, L(i), {"auditoria": ("43", "Дистант")}) for i in range(1, 4)]
    new = [L(i, auditoria="Дистант") for i in range(1, 4)]
    text = format_day_changes_full(date(2026, 9, 8), changes, new)
    assert "ИЗМЕНЕНИЯ" in text
    assert "08:30–09:50" in text
    assert "10:00–11:20" in text
    assert "11:30–12:50" in text
    assert "Дистант" in text
    assert "Всего изменений: 3" in text


def test_format_full_changes_summary_format():
    changes = [Change("modify", 1, 0, L(1), {"teacher": ("A", "B")})]
    text = format_day_changes_full(date(2026, 9, 8), changes, [L(1)])
    assert "+0" in text
    assert "-0" in text
    assert "~1" in text


# ---------- split_message ----------


def test_split_message_short():
    text = "коротко"
    assert split_message(text) == [text]


def test_split_message_exact_limit():
    text = "a" * VK_MESSAGE_LIMIT
    parts = split_message(text)
    assert len(parts) == 1


def test_split_message_long():
    text = "\n".join(["строка " + str(i) for i in range(1000)])
    parts = split_message(text, limit=200)
    assert len(parts) > 1
    # Ни одна часть не превышает лимит
    for p in parts:
        assert len(p) <= 200


def test_split_message_preserves_content():
    """Склейка обратно даёт исходный текст (без потери строк)."""
    text = "\n".join(f"строка-{i}" for i in range(100))
    parts = split_message(text, limit=50)
    restored = "\n".join(parts)
    assert restored == text


def test_split_message_never_breaks_line():
    """Одна длинная строка, если она меньше лимита, остаётся целиком."""
    long_line = "x" * 100
    text = f"short\n{long_line}\nshort"
    parts = split_message(text, limit=150)
    # Строка из 100 символов должна быть в одной части
    joined = "\n".join(parts)
    assert long_line in joined


def test_day_schedule_shows_territory_when_different():
    """Пара в чужом СП — показываем «СП-5» перед кабинетом."""
    d = date(2026, 9, 8)
    lessons = [L(1, auditoria="12", territory="(СП-5) МФЦПК")]
    text = format_day_schedule(d, lessons, home_territory="(СП-4) Энергетическое отделение")
    assert "СП-5" in text
    assert "каб. 12" in text


def test_day_schedule_omits_home_territory():
    """Пара в своём СП — не показываем СП."""
    d = date(2026, 9, 8)
    lessons = [L(1, auditoria="43", territory="(СП-4) Энергетическое отделение")]
    text = format_day_schedule(d, lessons, home_territory="(СП-4) Энергетическое отделение")
    assert "СП-4" not in text
    assert "каб. 43" in text


# ---------- format_week_changes_full ----------


def test_week_changes_full_empty_week():
    """Пустая неделя — только заголовок и итог."""
    start = date(2026, 9, 14)
    week = {start + timedelta(days=i): [] for i in range(6)}
    text = format_week_changes_full(start, {}, week)
    assert "⚠️ ИЗМЕНЕНИЯ: 14.09–19.09" in text
    assert "Всего изменений за неделю: 0" in text


def test_week_changes_full_header_range():
    """Заголовок — dd.mm–dd.mm от понедельника до воскресенья."""
    start = date(2026, 9, 14)
    week = {start + timedelta(days=i): [] for i in range(6)}
    text = format_week_changes_full(start, {}, week)
    assert text.startswith("⚠️ ИЗМЕНЕНИЯ: 14.09–19.09")


def test_week_changes_full_marks_changed_lessons():
    """Изменившиеся пары помечаются 🔔, остальные — нет."""
    start = date(2026, 9, 14)  # Пн
    week = {
        start: [L(1, teacher="Иванов"), L(2, teacher="Петров")],
        start + timedelta(days=1): [L(1, teacher="Сидоров")],
        start + timedelta(days=2): [],
        start + timedelta(days=3): [],
        start + timedelta(days=4): [],
        start + timedelta(days=5): [],
        start + timedelta(days=6): [],
    }
    changes = {start: [Change("modify", 1, 0, L(1, teacher="Смирнов"), {"teacher": ("Иванов", "Смирнов")})]}
    text = format_week_changes_full(start, changes, week)

    # Маркер должен быть только у пары 1 понедельника
    assert text.count(CHANGE_MARKER) == 2  # 1 — у пары, 1 — у заголовка дня
    # Проверим, что пары правильно помечены
    assert "🔔 1." in text
    assert "🔔 2." not in text


def test_week_changes_full_day_header_marker():
    """День с изменениями — маркер в заголовке дня."""
    start = date(2026, 9, 14)
    week = {start + timedelta(days=i): [L(1)] for i in range(7)}
    changes = {start: [Change("modify", 1, 0, L(1), {"teacher": ("A", "B")})]}
    text = format_week_changes_full(start, changes, week)
    assert "— 14.09 (Пн) 🔔 —" in text
    assert "— 15.09 (Вт) —" in text


def test_week_changes_full_shows_all_days():
    """Все 6 дней (Пн–Сб), включая пустые, отображаются."""
    start = date(2026, 9, 14)
    week = {
        start: [L(1)],
        start + timedelta(days=1): [],
        start + timedelta(days=2): [L(1)],
        start + timedelta(days=3): [],
        start + timedelta(days=4): [L(1)],
        start + timedelta(days=5): [],
        start + timedelta(days=6): [],
    }
    text = format_week_changes_full(start, {}, week)
    for d in ("14.09", "15.09", "16.09", "17.09", "18.09", "19.09"):
        assert d in text
    assert "20.09" not in text  # воскресенье не показываем


def test_week_changes_full_summary():
    """Сводка считает все изменения по дням."""
    start = date(2026, 9, 14)
    week = {start + timedelta(days=i): [L(1)] for i in range(3)}
    for i in range(3, 7):
        week[start + timedelta(days=i)] = []

    changes = {
        start: [Change("modify", 1, 0, L(1), {"teacher": ("A", "B")})],
        start + timedelta(days=1): [Change("add", 2, 0, L(2)), Change("remove", 3, 0, L(3))],
        start + timedelta(days=2): [Change("modify", 1, 0, L(1), {"auditoria": ("1", "2")})],
    }
    text = format_week_changes_full(start, changes, week)
    assert "Всего изменений за неделю: 4" in text
    assert "+1 / -1 / ~2" in text


def test_week_changes_full_uses_home_territory():
    """Корпус отображается, если отличается от домашнего."""
    start = date(2026, 9, 14)
    week = {
        start: [L(1, auditoria="12", territory="(СП-5) МФЦПК")],
        start + timedelta(days=1): [],
        start + timedelta(days=2): [],
        start + timedelta(days=3): [],
        start + timedelta(days=4): [],
        start + timedelta(days=5): [],
        start + timedelta(days=6): [],
    }
    text = format_week_changes_full(start, {}, week, home_territory="(СП-4) Энергетическое отделение")
    assert "СП-5" in text


# ---------- format_day_changes_full с маркером ----------


def test_day_changes_full_marks_changed_lessons():
    """В format_day_changes_full изменившиеся пары помечены 🔔."""
    d = date(2026, 9, 15)
    lessons = [L(1, teacher="A"), L(2, teacher="B"), L(3, teacher="C")]
    changes = [
        Change("modify", 1, 0, L(1, teacher="A-new"), {"teacher": ("A", "A-new")}),
        Change("modify", 3, 0, L(3, teacher="C-new"), {"teacher": ("C", "C-new")}),
    ]
    text = format_day_changes_full(d, changes, lessons)
    assert "🔔 1." in text
    assert "🔔 2." not in text
    assert "🔔 3." in text
