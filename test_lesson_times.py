"""Тесты для lesson_times.py."""

import pytest
from lesson_times import (
    get_lesson_time,
    get_day_schedule_times,
    is_lesson_day,
    MAX_LESSON,
)


# ---------- is_lesson_day ----------

@pytest.mark.parametrize("weekday, expected", [
    (0, True), (1, True), (2, True), (3, True), (4, True), (5, True),
    (6, False),
    (-1, False), (7, False),
])
def test_is_lesson_day(weekday, expected):
    assert is_lesson_day(weekday) is expected


# ---------- Воскресенье ----------

def test_sunday_returns_none():
    for n in range(1, MAX_LESSON + 1):
        assert get_lesson_time(6, n) is None


def test_sunday_day_schedule_empty():
    assert get_day_schedule_times(6) == {}


# ---------- Вторник–пятница (эталон) ----------

@pytest.mark.parametrize("weekday", [1, 2, 3, 4])
def test_weekday_times(weekday):
    """Проверяем, что Вт–Пт дают расписание, совпадающее с исходным."""
    expected = {
        1: "08:30–09:50",
        2: "10:00–11:20",
        3: "11:30–12:50",
        4: "13:40–15:00",
        5: "15:10–16:30",
        6: "16:40–18:00",
        7: "18:10–19:30",
        8: "19:40–21:00",
    }
    for n, exp in expected.items():
        assert get_lesson_time(weekday, n) == exp, f"день={weekday}, пара={n}"


# ---------- Понедельник (+30 минут) ----------

def test_monday_shifted():
    """Понедельник — всё на 30 минут позже вторника."""
    expected = {
        1: "09:00–10:20",
        2: "10:30–11:50",
        3: "12:00–13:20",
        4: "14:10–15:30",
        5: "15:40–17:00",
        6: "17:10–18:30",
        7: "18:40–20:00",
        8: "20:10–21:30",
    }
    for n, exp in expected.items():
        assert get_lesson_time(0, n) == exp, f"пара={n}"


# ---------- Суббота (60 минут, без обеда) ----------

def test_saturday_short_lessons():
    """Суббота — пары по 60 минут, перерыв 10 минут, обеда нет."""
    expected = {
        1: "08:30–09:30",
        2: "09:40–10:40",
        3: "10:50–11:50",
        4: "12:00–13:00",   # без обеда: +10 мин, а не +50
        5: "13:10–14:10",
        6: "14:20–15:20",
        7: "15:30–16:30",
        8: "16:40–17:40",
    }
    for n, exp in expected.items():
        assert get_lesson_time(5, n) == exp, f"пара={n}"


# ---------- Границы ----------

@pytest.mark.parametrize("num_lesson", [0, -1, 9, 100])
def test_out_of_range_lesson(num_lesson):
    assert get_lesson_time(1, num_lesson) is None


def test_day_schedule_has_8_lessons():
    for weekday in range(6):  # Пн–Сб
        sched = get_day_schedule_times(weekday)
        assert len(sched) == MAX_LESSON
        assert set(sched.keys()) == set(range(1, MAX_LESSON + 1))


# ---------- Инварианты ----------

def test_times_are_monotonic():
    """Каждая следующая пара начинается позже предыдущей."""
    for weekday in range(6):
        sched = get_day_schedule_times(weekday)
        starts = [sched[n].split("–")[0] for n in range(1, MAX_LESSON + 1)]
        assert starts == sorted(starts), f"день={weekday}: пары не по порядку"


def test_no_overlap_within_day():
    """Конец предыдущей пары строго раньше начала следующей."""
    for weekday in range(6):
        sched = get_day_schedule_times(weekday)
        for n in range(1, MAX_LESSON):
            end_prev = sched[n].split("–")[1]
            start_next = sched[n + 1].split("–")[0]
            assert end_prev < start_next, f"день={weekday}, пара={n}/{n+1}"