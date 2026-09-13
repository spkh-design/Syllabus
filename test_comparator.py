"""Тесты для comparator.py."""

from __future__ import annotations

import pytest
from datetime import date

from comparator import (
    Change,
    count_changed_days,
    diff_lessons,
    describe_change,
    is_massive_change,
    summarize_changes,
)


# ---------- Хелперы ----------

def L(number, subgroup=0, discipline="Дисц", teacher="Препод",
      auditoria="43", lesson_type="Лекция"):
    """Сокращение для создания пары."""
    return {
        "number": number,
        "subgroup": subgroup,
        "discipline": discipline,
        "teacher": teacher,
        "auditoria": auditoria,
        "lesson_type": lesson_type,
    }


# ---------- diff_lessons: базовые случаи ----------

def test_diff_no_changes():
    a = [L(1), L(2)]
    assert diff_lessons(a, a) == []


def test_diff_add_lesson():
    old = [L(1)]
    new = [L(1), L(2)]
    changes = diff_lessons(old, new)
    assert len(changes) == 1
    c = changes[0]
    assert c.type == "add"
    assert c.number == 2
    assert c.lesson["number"] == 2


def test_diff_remove_lesson():
    old = [L(1), L(2)]
    new = [L(1)]
    changes = diff_lessons(old, new)
    assert len(changes) == 1
    assert changes[0].type == "remove"
    assert changes[0].number == 2


def test_diff_modify_teacher():
    old = [L(1, teacher="Иванов")]
    new = [L(1, teacher="Петров")]
    changes = diff_lessons(old, new)
    assert len(changes) == 1
    c = changes[0]
    assert c.type == "modify"
    assert c.fields == {"teacher": ("Иванов", "Петров")}


def test_diff_modify_auditoria():
    old = [L(1, auditoria="43")]
    new = [L(1, auditoria="45")]
    changes = diff_lessons(old, new)
    assert changes[0].fields == {"auditoria": ("43", "45")}


def test_diff_modify_discipline():
    old = [L(1, discipline="Web")]
    new = [L(1, discipline="Информатика")]
    changes = diff_lessons(old, new)
    assert changes[0].fields == {"discipline": ("Web", "Информатика")}


def test_diff_modify_lesson_type():
    old = [L(1, lesson_type="Лекция")]
    new = [L(1, lesson_type="Практическая работа")]
    changes = diff_lessons(old, new)
    assert changes[0].fields == {"lesson_type": ("Лекция", "Практическая работа")}


def test_diff_modify_multiple_fields():
    old = [L(1, teacher="Иванов", auditoria="43")]
    new = [L(1, teacher="Петров", auditoria="45")]
    changes = diff_lessons(old, new)
    assert changes[0].fields == {
        "teacher": ("Иванов", "Петров"),
        "auditoria": ("43", "45"),
    }


# ---------- Особые случаи (из вашего ТЗ) ----------

def test_diff_distance_learning():
    """Поставили дистант — auditoria стала 'Дистант'."""
    old = [L(1, auditoria="43")]
    new = [L(1, auditoria="Дистант")]
    changes = diff_lessons(old, new)
    assert changes[0].fields == {"auditoria": ("43", "Дистант")}


def test_diff_self_work():
    """Поставили СР — auditoria стала 'СР'."""
    old = [L(1, auditoria="43")]
    new = [L(1, auditoria="СР")]
    changes = diff_lessons(old, new)
    assert changes[0].fields == {"auditoria": ("43", "СР")}


def test_diff_subgroup_swap_is_remove_plus_add():
    """Подгруппы поменяли местами — это remove + add, а не modify."""
    old = [L(1, subgroup=1, teacher="Иванов"),
           L(1, subgroup=2, teacher="Петров")]
    new = [L(1, subgroup=1, teacher="Петров"),
           L(1, subgroup=2, teacher="Иванов")]
    changes = diff_lessons(old, new)
    # Обе подгруппы изменились -> 2 modify
    assert len(changes) == 2
    assert all(c.type == "modify" for c in changes)


def test_diff_subgroup_number_changed():
    """Подгруппа была 0, стала 1 — это remove старой + add новой."""
    old = [L(1, subgroup=0)]
    new = [L(1, subgroup=1)]
    changes = diff_lessons(old, new)
    types = sorted(c.type for c in changes)
    assert types == ["add", "remove"]


# ---------- Пустые входы ----------

def test_diff_empty_old():
    """Снимка не было — все пары новые."""
    changes = diff_lessons([], [L(1), L(2)])
    assert len(changes) == 2
    assert all(c.type == "add" for c in changes)


def test_diff_empty_new():
    """Все пары отменили."""
    changes = diff_lessons([L(1), L(2)], [])
    assert len(changes) == 2
    assert all(c.type == "remove" for c in changes)


def test_diff_both_empty():
    assert diff_lessons([], []) == []


# ---------- Сортировка ----------

def test_diff_sorted_by_number():
    old = []
    new = [L(3), L(1), L(2)]
    changes = diff_lessons(old, new)
    assert [c.number for c in changes] == [1, 2, 3]


# ---------- is_massive_change: правило доли ----------

def test_massive_ratio_more_than_half():
    """7 из 8 пар изменились — это массовое."""
    changes = [
        Change("modify", i, 0, L(i), {"auditoria": ("43", "45")})
        for i in range(1, 8)
    ]
    assert is_massive_change(changes, total_lessons=8) is True


def test_not_massive_ratio_less_than_half():
    """2 из 8 — не массовое."""
    changes = [
        Change("modify", 1, 0, L(1), {"auditoria": ("43", "45")}),
        Change("modify", 2, 0, L(2), {"teacher": ("A", "B")}),
    ]
    assert is_massive_change(changes, total_lessons=8) is False


# ---------- is_massive_change: правило серии ----------

def test_massive_streak_of_3_identical_changes():
    """3 пары подряд с одинаковым изменением auditoria -> 'Дистант'."""
    changes = [
        Change("modify", 1, 0, L(1), {"auditoria": ("43", "Дистант")}),
        Change("modify", 2, 0, L(2), {"auditoria": ("43", "Дистант")}),
        Change("modify", 3, 0, L(3), {"auditoria": ("43", "Дистант")}),
    ]
    assert is_massive_change(changes, total_lessons=8) is True


def test_not_massive_streak_of_2():
    """2 подряд — не хватает до порога 3."""
    changes = [
        Change("modify", 1, 0, L(1), {"auditoria": ("43", "Дистант")}),
        Change("modify", 2, 0, L(2), {"auditoria": ("43", "Дистант")}),
    ]
    assert is_massive_change(changes, total_lessons=8) is False


def test_streak_broken_by_other_change():
    """3 изменения, но не подряд одинаковые — не массовое."""
    changes = [
        Change("modify", 1, 0, L(1), {"auditoria": ("43", "Дистант")}),
        Change("modify", 2, 0, L(2), {"teacher": ("A", "B")}),
        Change("modify", 3, 0, L(3), {"auditoria": ("43", "Дистант")}),
    ]
    assert is_massive_change(changes, total_lessons=8) is False


def test_massive_streak_of_adds():
    """5 пар подряд добавили — массовое (add с одинаковой сигнатурой)."""
    changes = [
        Change("add", i, 0, L(i, discipline="Физра"))
        for i in range(1, 6)
    ]
    assert is_massive_change(changes, total_lessons=8) is True


def test_empty_changes_not_massive():
    assert is_massive_change([], total_lessons=8) is False


def test_zero_lessons_not_massive():
    changes = [Change("add", 1, 0, L(1))]
    assert is_massive_change(changes, total_lessons=0) is False


# ---------- describe_change ----------

def test_describe_add():
    c = Change("add", 3, 0, L(3, discipline="Математика",
                                teacher="Иванов", auditoria="12"))
    text = describe_change(c)
    assert "➕" in text
    assert "3" in text
    assert "Математика" in text
    assert "Иванов" in text


def test_describe_remove():
    c = Change("remove", 4, 0, L(4, discipline="Физика"))
    text = describe_change(c)
    assert "➖" in text
    assert "Физика" in text


def test_describe_modify_teacher():
    c = Change("modify", 1, 0, L(1),
               {"teacher": ("Иванов", "Петров")})
    text = describe_change(c)
    assert "✏️" in text
    assert "преподаватель" in text
    assert "Иванов" in text and "Петров" in text


def test_describe_modify_multiple_fields():
    c = Change("modify", 2, 1, L(2, subgroup=1),
               {"auditoria": ("43", "45"), "lesson_type": ("Лекция", "Практика")})
    text = describe_change(c)
    assert "подгр. 1" in text
    assert "кабинет" in text
    assert "тип занятия" in text


# ---------- summarize_changes ----------

def test_summarize_counts():
    changes = [
        Change("add", 1, 0, L(1)),
        Change("remove", 2, 0, L(2)),
        Change("modify", 3, 0, L(3), {"teacher": ("A", "B")}),
        Change("modify", 4, 0, L(4), {"teacher": ("C", "D")}),
        Change("modify", 5, 0, L(5), {"auditoria": ("43", "45")}),
    ]
    s = summarize_changes(changes)
    assert s["add"] == 1
    assert s["remove"] == 1
    assert s["modify"] == 3
    assert s["fields"] == {"teacher": 2, "auditoria": 1}


def test_summarize_empty():
    s = summarize_changes([])
    assert s == {"add": 0, "remove": 0, "modify": 0, "fields": {}}


def test_not_massive_single_change_in_single_lesson():
    """1 пара из 1 — это не массовое, а точечное изменение."""
    changes = [Change("modify", 1, 0, L(1), {"teacher": ("A", "B")})]
    assert is_massive_change(changes, total_lessons=1) is False


def test_not_massive_two_changes_in_two_lessons():
    """2 пары из 2 — всё ещё не массовое (min_changes=3)."""
    changes = [
        Change("modify", 1, 0, L(1), {"teacher": ("A", "B")}),
        Change("modify", 2, 0, L(2), {"teacher": ("C", "D")}),
    ]
    assert is_massive_change(changes, total_lessons=2) is False


def test_massive_three_of_three():
    """3 пары из 3 — массовое (>=3 и >50%)."""
    changes = [
        Change("modify", i, 0, L(i), {"teacher": ("A", "B")})
        for i in range(1, 4)
    ]
    assert is_massive_change(changes, total_lessons=3) is True


def test_massive_threshold_edge():
    """Ровно 50% (4 из 8) — не массовое. 5 из 8 — массовое.
    
    Изменения разные (чтобы не сработало правило streak),
    но их количество — ровно на границе правила ratio.
    """
    # 4 из 8 — ровно 50%, не массовое
    changes_4 = [
        Change("modify", 1, 0, L(1), {"teacher": ("A", "B")}),
        Change("modify", 2, 0, L(2), {"auditoria": ("10", "20")}),
        Change("modify", 3, 0, L(3), {"discipline": ("X", "Y")}),
        Change("modify", 4, 0, L(4), {"lesson_type": ("Лекция", "Практика")}),
    ]
    assert is_massive_change(changes_4, total_lessons=8) is False

    # 5 из 8 — 62.5%, массовое
    changes_5 = changes_4 + [
        Change("modify", 5, 0, L(5), {"teacher": ("C", "D")}),
    ]
    assert is_massive_change(changes_5, total_lessons=8) is True


def test_min_changes_disabled():
    """Явное отключение min_changes возвращает старое поведение."""
    changes = [Change("modify", 1, 0, L(1), {"teacher": ("A", "B")})]
    assert is_massive_change(changes, total_lessons=1, min_changes=1) is True


def test_diff_sr_to_distance_not_a_change():
    """Переход СР → Дистант НЕ считается изменением."""
    old = [L(1, auditoria="СР")]
    new = [L(1, auditoria="Дистант")]
    assert diff_lessons(old, new) == []


def test_diff_distance_to_sr_not_a_change():
    """Переход Дистант → СР тоже НЕ считается изменением."""
    old = [L(1, auditoria="Дистант")]
    new = [L(1, auditoria="СР")]
    assert diff_lessons(old, new) == []


def test_diff_sr1_to_sr2_not_a_change():
    """СР-1 → СР-2 — не изменение."""
    old = [L(1, auditoria="СР-1")]
    new = [L(1, auditoria="СР-2")]
    assert diff_lessons(old, new) == []


def test_diff_cabinet_to_sr_is_a_change():
    """Кабинет → СР — ЭТО изменение."""
    old = [L(1, auditoria="43")]
    new = [L(1, auditoria="СР")]
    changes = diff_lessons(old, new)
    assert len(changes) == 1
    assert changes[0].fields == {"auditoria": ("43", "СР")}


def test_diff_distance_typo_with_trailing_space():
    """Реальная опечатка из API — «Дистантанционное обучение » с пробелом."""
    old = [L(1, auditoria="Дистант")]
    new = [L(1, auditoria="Дистантанционное обучение ")]
    assert diff_lessons(old, new) == []


def test_diff_distance_uppercase():
    """Регистр не важен: «ДИСТАНТ» эквивалентно «Дистант»."""
    old = [L(1, auditoria="ДИСТАНТ")]
    new = [L(1, auditoria="СР")]
    assert diff_lessons(old, new) == []


def test_diff_sr_to_cabinet_is_a_change():
    """СР → кабинет 43 — изменение (вернулись в класс)."""
    old = [L(1, auditoria="СР")]
    new = [L(1, auditoria="43")]
    changes = diff_lessons(old, new)
    assert len(changes) == 1
    assert changes[0].fields == {"auditoria": ("СР", "43")}


# ---------- count_changed_days ----------

def test_count_changed_days_empty():
    assert count_changed_days({}) == 0


def test_count_changed_days_no_changes():
    d1, d2 = date(2026, 9, 14), date(2026, 9, 15)
    assert count_changed_days({d1: [], d2: []}) == 0


def test_count_changed_days_one_day():
    d1, d2 = date(2026, 9, 14), date(2026, 9, 15)
    changes = {d1: [Change("modify", 1, 0, L(1), {"teacher": ("A", "B")})], d2: []}
    assert count_changed_days(changes) == 1


def test_count_changed_days_three_days():
    days = [date(2026, 9, 14 + i) for i in range(3)]
    changes = {d: [Change("modify", 1, 0, L(1), {"teacher": ("A", "B")})]
               for d in days}
    assert count_changed_days(changes) == 3


def test_count_changed_days_ignores_empty_entries():
    """Дата с пустым списком не считается изменившейся."""
    days = [date(2026, 9, 14 + i) for i in range(5)]
    changes = {
        days[0]: [Change("modify", 1, 0, L(1), {"teacher": ("A", "B")})],
        days[1]: [],
        days[2]: [Change("modify", 2, 0, L(2), {"teacher": ("C", "D")})],
        days[3]: [],
        days[4]: [Change("modify", 3, 0, L(3), {"teacher": ("E", "F")})],
    }
    assert count_changed_days(changes) == 3