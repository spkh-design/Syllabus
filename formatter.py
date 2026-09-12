"""Формирование текстов для ВК.

Модуль не знает про API, БД и ВК — только про строки.
На вход — нормализованные данные, на выход — готовый текст.

Публичный API:
    format_day_schedule(day, lessons)                   -> str
    format_week_schedule(start_date, week)              -> str
    format_changes(day, changes, new_lessons)           -> str
    format_day_changes_full(day, changes, new_lessons)  -> str
    split_message(text, limit)                          -> list[str]
"""

from __future__ import annotations

import re
from datetime import date as date_type, timedelta

from comparator import Change, describe_change, summarize_changes
from lesson_times import get_lesson_time

# Ограничение длины одного сообщения ВК
VK_MESSAGE_LIMIT = 4000

# Названия дней недели
WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _weekday_short(d: date_type) -> str:
    return WEEKDAYS_RU[d.weekday()]


def _short_territory(full_name: str) -> str:
    """'(СП-5) Многофункциональный центр...' -> 'СП-5'.

    Если не удалось — возвращает пустую строку.
    """
    m = re.search(r"СП-\d", full_name)
    return m.group(0) if m else ""


def _format_lesson_line(
    lesson: dict, weekday: int, home_territory: str = "",
) -> str:
    """Одна пара в 4 строках.

    Если territory пары не совпадает с home_territory (или home пустой,
    а territory непустой) — добавляем «[СП-5]» после кабинета.
    """
    number = lesson["number"]
    subgroup = lesson["subgroup"]
    time = get_lesson_time(weekday, number) or f"пара {number}"
    sub = f" (подгр. {subgroup})" if subgroup else ""

    # Формируем описание локации
    auditoria = lesson.get("auditoria", "—")
    territory = (lesson.get("territory") or "").strip()

    location = f"каб. {auditoria}"
    if territory and territory != home_territory:
        # Вытащим «СП-N» из названия типа «(СП-5) МФЦПК»
        short_territory = _short_territory(territory)
        if short_territory:
            location = f"{short_territory}, каб. {auditoria}"
        else:
            location = f"{territory}, каб. {auditoria}"

    lines = [
        f"{number}. {time}{sub}",
        f"   {lesson.get('discipline', '—')}",
        f"   {lesson.get('lesson_type', '—')} | {location} | "
        f"{lesson.get('teacher', '—')}",
    ]
    return "\n".join(lines)


def _format_day_header(d: date_type, *, prefix: str = "") -> str:
    """Шапка сообщения: дата + день недели. Опционально — префикс."""
    wd = _weekday_short(d)
    date_str = d.strftime("%d.%m.%Y")
    head = f"{date_str} ({wd})"
    if prefix:
        head = f"{prefix} {head}"
    return head


def format_day_schedule(day: date_type, lessons: list[dict], home_territory: str = "",) -> str:
    """Расписание одного дня. Без пары — 'Пар нет'."""
    head = _format_day_header(day)
    if not lessons:
        return f"📅 {head}\n🎉 Пар нет"

    blocks = [f"📅 {head}"]
    for lesson in lessons:
        blocks.append(_format_lesson_line(lesson, day.weekday(), home_territory))
    return "\n\n".join(blocks)


def format_week_schedule(
    start_date: date_type, week: dict[date_type, list[dict]], home_territory: str = "",
) -> str:
    """Расписание на неделю. Все 7 дней, включая пустые."""
    head = (f"📅 Расписание на неделю "
            f"с {start_date.strftime('%d.%m.%Y')} "
            f"по {(start_date + timedelta(days=6)).strftime('%d.%m.%Y')}")
    blocks = [head]

    for offset in range(7):
        d = start_date + timedelta(days=offset)
        lessons = week.get(d, [])
        wd = _weekday_short(d)

        if not lessons:
            blocks.append(f"— {d.strftime('%d.%m')} ({wd}) — пар нет")
            continue

        blocks.append(f"— {d.strftime('%d.%m')} ({wd}) —")
        for lesson in lessons:
            blocks.append(_format_lesson_line(lesson, d.weekday(), home_territory))
        blocks.append("")  # пустая строка между днями

    return "\n".join(blocks).rstrip()


def format_changes(
    day: date_type, changes: list[Change], new_lessons: list[dict],
) -> str:
    """Короткое сообщение об изменениях (diff).

    Если изменений нет — вернёт пустую строку (вызывающий код
    решает, что делать).
    """
    if not changes:
        return ""

    head = _format_day_header(day, prefix="⚠️ ИЗМЕНЕНИЯ:")
    lines = [head, ""]

    for c in changes:
        lines.append(describe_change(c))
        lines.append("")  # пустая строка между изменениями

    summary = summarize_changes(changes)
    tail = (f"Всего: добавлено {summary['add']}, "
            f"отменено {summary['remove']}, "
            f"изменено {summary['modify']}")
    lines.append(tail)

    return "\n".join(lines).rstrip()


def format_day_changes_full(
    day: date_type, changes: list[Change], new_lessons: list[dict], home_territory: str = "",
) -> str:
    """Полное расписание дня с пометкой об изменениях.

    Используется при массовых изменениях: например, на весь день
    поставили дистант — нет смысла перечислять 8 пар по одной,
    лучше показать расписание целиком.
    """
    head = _format_day_header(day, prefix="⚠️ ИЗМЕНЕНИЯ:")
    if not new_lessons:
        return f"{head}\n🎉 Пар нет (все отменены)"

    blocks = [head, ""]
    for lesson in new_lessons:
        blocks.append(_format_lesson_line(lesson, day.weekday(), home_territory))

    summary = summarize_changes(changes)
    blocks.append("")
    blocks.append(
        f"Всего изменений: {len(changes)} "
        f"(+{summary['add']} / -{summary['remove']} / ~{summary['modify']})"
    )

    return "\n\n".join(blocks)


def split_message(text: str, limit: int = VK_MESSAGE_LIMIT) -> list[str]:
    """Режет длинный текст на части, не разрывая строки.

    Args:
        text:  исходный текст.
        limit: максимальная длина одной части.

    Returns:
        Список частей. Если текст короче limit — список из одного элемента.
    """
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.split("\n"):
        # +1 на символ перевода строки
        line_len = len(line) + 1
        if current_len + line_len > limit and current:
            parts.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        parts.append("\n".join(current))

    return parts