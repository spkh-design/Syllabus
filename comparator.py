"""Сравнение снимков расписания.

Модуль принимает два списка пар (старый и новый снимок),
возвращает структурированный список изменений и умеет определять,
является ли изменение «массовым» (тогда нужно слать полное расписание).

Публичный API:
    diff_lessons(old, new)                -> list[Change]
    is_massive_change(changes, total)     -> bool
    describe_change(change)               -> str
    summarize_changes(changes)            -> dict
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

# Поля пары, которые мы отслеживаем
TRACKED_FIELDS = ("discipline", "teacher", "auditoria", "lesson_type", "territory")

# Порог «массовости»: если изменилось больше этой доли пар — шлём полное
MASSIVE_RATIO = 0.5

# Порог «подряд»: если одинаковое изменение у стольких пар — шлём полное
MASSIVE_STREAK = 3

# Минимальное число изменений, при котором срабатывает правило доли
MASSIVE_MIN_CHANGES = 3

ChangeType = Literal["add", "remove", "modify"]


@dataclass
class Change:
    """Одно изменение в расписании.

    Attributes:
        type:    'add' | 'remove' | 'modify'
        number:  номер пары
        subgroup: подгруппа (0 — вся группа)
        lesson:  новая (add/modify) или старая (remove) пара
        fields:  для modify — {поле: (было, стало)}
    """
    type: ChangeType
    number: int
    subgroup: int
    lesson: dict
    fields: dict[str, tuple[str, str]] = field(default_factory=dict)


def _key(lesson: dict) -> tuple[int, int]:
    """Ключ пары: (номер, подгруппа)."""
    return lesson["number"], lesson["subgroup"]


def diff_lessons(old: list[dict], new: list[dict]) -> list[Change]:
    """Сравнивает два снимка и возвращает список изменений.

    Args:
        old: старый снимок (пустой список — если снимка не было).
        new: новый снимок.

    Returns:
        Список Change, отсортированный по (номер, подгруппа).
    """
    old_map = {_key(l): l for l in old}
    new_map = {_key(l): l for l in new}

    changes: list[Change] = []

    for k in sorted(set(old_map) | set(new_map)):
        o = old_map.get(k)
        n = new_map.get(k)
        number, subgroup = k

        if o is None and n is not None:
            changes.append(Change(
                type="add", number=number, subgroup=subgroup, lesson=n,
            ))
        elif n is None and o is not None:
            changes.append(Change(
                type="remove", number=number, subgroup=subgroup, lesson=o,
            ))
        elif o is not None and n is not None:
            fields = {}
            for f in TRACKED_FIELDS:
                if o.get(f) != n.get(f):
                    fields[f] = (o.get(f, "—"), n.get(f, "—"))
            if fields:
                changes.append(Change(
                    type="modify", number=number, subgroup=subgroup,
                    lesson=n, fields=fields,
                ))

    return changes


def is_massive_change(
    changes: list[Change],
    total_lessons: int,
    *,
    ratio: float = MASSIVE_RATIO,
    streak: int = MASSIVE_STREAK,
    min_changes: int = MASSIVE_MIN_CHANGES,
) -> bool:
    """Определяет, «массовое» ли изменение.

    Возвращает True, если:
      - изменилось > `ratio` от общего числа пар И их не меньше `min_changes`, ИЛИ
      - у ≥ `streak` пар подряд одинаковое изменение одного и того же поля.

    Пример «массового»: на весь день поставили Дистант —
        у 4 пар подряд auditoria: ('43', 'Дистант').

    Пример НЕ массового: у единственной пары сменился преподаватель.
    """
    if not changes:
        return False

    # Правило 1: доля изменившихся пар > порога И их минимум min_changes
    # (total_lessons может быть 0 — тогда правило не применяется)
    if total_lessons > 0 and len(changes) >= min_changes:
        if len(changes) / total_lessons > ratio:
            return True

    # Правило 2: одинаковое изменение одного поля у streak пар подряд
    if streak <= 1:
        return False

    max_run = 1
    current_run = 1
    prev_signature: Optional[tuple] = None

    sorted_changes = sorted(changes, key=lambda c: (c.number, c.subgroup))
    for c in sorted_changes:
        sig = _change_signature(c)
        if sig is not None and sig == prev_signature:
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 1
        prev_signature = sig

    return max_run >= streak


def _change_signature(change: Change) -> Optional[tuple]:
    """Подпись изменения для поиска «серий».

    Для modify — (тип='modify', поле, было, стало).
    Для add/remove — (тип, disciplina, teacher, auditoria, lesson_type) —
        если у серии добавлений/удалений одинаковые значения, считаем
        это массовым (например, отменили 5 пар подряд).

    Возвращает None, если подпись не имеет смысла.
    """
    if change.type == "modify":
        if len(change.fields) != 1:
            return None
        (fname, (old_v, new_v)), = change.fields.items()
        return ("modify", fname, old_v, new_v)

    if change.type in ("add", "remove"):
        return (
            change.type,
            change.lesson.get("discipline"),
            change.lesson.get("teacher"),
            change.lesson.get("auditoria"),
            change.lesson.get("lesson_type"),
            change.lesson.get("territory"),
        )

    return None


def _location(lesson: dict) -> str:
    """'каб. 12' или 'СП-5, каб. 12' — если корпус указан."""
    auditoria = lesson.get("auditoria", "—")
    territory = (lesson.get("territory") or "").strip()
    if territory:
        m = re.search(r"СП-\d", territory)
        short = m.group(0) if m else territory
        return f"{short}, каб. {auditoria}"
    return f"каб. {auditoria}"


def describe_change(change: Change) -> str:
    """Человекочитаемое описание одного изменения (одна строка)."""
    sub = f" (подгр. {change.subgroup})" if change.subgroup else ""
    head = f"Пара {change.number}{sub}"

    if change.type == "add":
        l = change.lesson
        return (f"➕ {head}: добавлена — {l.get('discipline', '—')} "
                f"({l.get('lesson_type', '—')}), {_location(l)}, "
                f"{l.get('teacher', '—')}")

    if change.type == "remove":
        l = change.lesson
        return (f"➖ {head}: отменена — была {l.get('discipline', '—')} "
                f"({l.get('lesson_type', '—')}), {_location(l)}, "
                f"{l.get('teacher', '—')}")

    # modify
    field_names = {
        "discipline": "дисциплина",
        "teacher": "преподаватель",
        "auditoria": "кабинет",
        "lesson_type": "тип занятия",
        "territory": "корпус",
    }
    parts = []
    for f, (old_v, new_v) in change.fields.items():
        parts.append(f"{field_names.get(f, f)}: «{old_v}» → «{new_v}»")

    l = change.lesson
    return f"✏️ {head}: {l.get('discipline', '—')}; " + "; ".join(parts)


def summarize_changes(changes: list[Change]) -> dict:
    """Краткая сводка: сколько каких изменений и затронутые поля."""
    summary = {"add": 0, "remove": 0, "modify": 0, "fields": {}}
    for c in changes:
        summary[c.type] += 1
        for f in c.fields:
            summary["fields"][f] = summary["fields"].get(f, 0) + 1
    return summary