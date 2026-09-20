"""Расчёт времени пар по дню недели и номеру пары.

API колледжа не отдаёт время занятий, поэтому оно вычисляется здесь.
Правила:
  - Пн:      1-я пара с 09:00, длительность 80 мин, обед 50 мин после 3-й.
  - Вт–Пт:   1-я пара с 08:30, длительность 80 мин, обед 50 мин после 3-й.
  - Сб:      1-я пара с 08:30, длительность 60 мин, без обеда.
  - Вс:      пар нет.
  - Перерыв между остальными парами — 10 минут.
  - Пары нумеруются с 1 до 8.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

# Базовые значения для расчёта. Используем произвольную дату —
# нам важны только часы и минуты.
_BASE_DATE = datetime(2000, 1, 1, 0, 0)

# Длительности пар (минуты)
_LESSON_DURATION = {0: 80, 1: 80, 2: 80, 3: 80, 4: 80, 5: 60}  # Пн  # Вт–Пт  # Сб

# Время начала 1-й пары
_FIRST_LESSON_START = {0: (9, 0), 1: (8, 30), 2: (8, 30), 3: (8, 30), 4: (8, 30), 5: (8, 30)}  # Пн  # Вт–Пт  # Сб

# Обеденный перерыв после 3-й пары (минуты), 0 — обеда нет
_LUNCH_BREAK = {0: 50, 1: 50, 2: 50, 3: 50, 4: 50, 5: 0}  # Пн–Пт  # Сб — обеда нет

# Обычный перерыв между парами
_REGULAR_BREAK = 10

# Максимальный номер пары
MAX_LESSON = 8


def is_lesson_day(weekday: int) -> bool:
    """True, если в этот день недели вообще могут быть пары."""
    return 0 <= weekday <= 5  # Пн–Сб


def get_lesson_time(weekday: int, num_lesson: int) -> Optional[str]:
    """Возвращает строку 'HH:MM–HH:MM' для пары или None.

    Args:
        weekday:   0=Пн, 1=Вт, ..., 5=Сб, 6=Вс.
        num_lesson: номер пары (1..8).

    Returns:
        Строка вида '08:30–09:50' или None, если пары нет
        (воскресенье, номер вне диапазона).
    """
    if not is_lesson_day(weekday):
        return None
    if not (1 <= num_lesson <= MAX_LESSON):
        return None

    # Считаем от начала 1-й пары, добавляя длительности и перерывы
    hour, minute = _FIRST_LESSON_START[weekday]
    current = _BASE_DATE.replace(hour=hour, minute=minute)
    duration = timedelta(minutes=_LESSON_DURATION[weekday])
    lunch = _LUNCH_BREAK[weekday]

    for n in range(1, num_lesson):
        # После 3-й пары — обед, если он есть в этот день (lunch > 0).
        # Во всех остальных случаях — обычный перерыв 10 минут.
        if n == 3 and lunch > 0:
            break_min = lunch
        else:
            break_min = _REGULAR_BREAK
        current += duration + timedelta(minutes=break_min)

    end = current + duration
    return f"{current.strftime('%H:%M')}–{end.strftime('%H:%M')}"


def get_day_schedule_times(weekday: int) -> dict[int, str]:
    """Возвращает {номер_пары: 'HH:MM–HH:MM'} для всего дня.

    Если пары в этот день быть не может — пустой словарь.
    """
    if not is_lesson_day(weekday):
        return {}
    return {n: get_lesson_time(weekday, n) for n in range(1, MAX_LESSON + 1) if get_lesson_time(weekday, n) is not None}
