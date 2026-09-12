"""Тесты для formatter.py."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from comparator import Change
from formatter import (
    VK_MESSAGE_LIMIT,
    format_changes,
    format_day_changes_full,
    format_day_schedule,
    format_week_schedule,
    split_message,
)


# ---------- Хелперы ----------

def L(number, subgroup=0, discipline="Дисциплина", teacher="Преподаватель",
      auditoria="43", lesson_type="Лекция", territory="",):
    return {
        "number": number, "subgroup": subgroup, "discipline": discipline,
        "teacher": teacher, "auditoria": auditoria, "lesson_type": lesson_type, "territory": territory,
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
    """Пустые дни должны быть отмечены."""
    start = date(2026, 9, 7)  # понедельник
    week = {start + timedelta(days=i): [] for i in range(7)}
    text = format_week_schedule(start, week)
    assert "07.09" in text
    assert "13.09" in text
    assert text.count("пар нет") == 7


def test_week_schedule_with_lessons():
    start = date(2026, 9, 7)
    week = {
        start: [L(1, discipline="Математика")],
        start + timedelta(days=1): [L(1, discipline="Русский")],
    }
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
    assert "13.09.2026" in text


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
    changes = [
        Change("modify", i, 0, L(i), {"auditoria": ("43", "Дистант")})
        for i in range(1, 4)
    ]
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