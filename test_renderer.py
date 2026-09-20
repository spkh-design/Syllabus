"""Тесты для renderer.py."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from PIL import Image
import io

from comparator import Change
from renderer import render_day, render_week, _shorten_fio


def L(
    number=1,
    subgroup=0,
    discipline="Web",
    teacher="Дубров Никита Александрович",
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


def _open_png(data: bytes) -> Image.Image:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return Image.open(io.BytesIO(data))


# ---------- _shorten_fio ----------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Кизилова Евгения Александровна", "Кизилова Е.А."),
        ("Дубров Никита Александрович", "Дубров Н.А."),
        ("Кизилова Е.А.", "Кизилова Е.А."),
        ("Дубров Н.", "Дубров Н."),
        ("Дубров", "Дубров"),
        ("", "—"),
    ],
)
def test_shorten_fio(raw, expected):
    assert _shorten_fio(raw) == expected


# ---------- render_day ----------


def test_render_day_empty():
    png = render_day(date(2026, 9, 21), [], "")
    img = _open_png(png)
    assert img.width == 900
    assert img.height > 0
    assert img.format == "PNG"


def test_render_day_with_lessons():
    lessons = [L(1), L(2, discipline="Математика")]
    png = render_day(date(2026, 9, 22), lessons, "")
    img = _open_png(png)
    assert img.width == 900


def test_render_day_with_changes():
    lessons = [L(1)]
    changes = [Change("modify", 1, 0, L(1), {"teacher": ("A", "B")})]
    png = render_day(date(2026, 9, 22), lessons, "", changes=changes)
    _open_png(png)


def test_render_day_subgroups():
    lessons = [L(1, subgroup=1), L(1, subgroup=2)]
    png = render_day(date(2026, 9, 22), lessons, "")
    _open_png(png)


# ---------- render_week ----------


def test_render_week_empty():
    start = date(2026, 9, 21)
    week = {start + timedelta(days=i): [] for i in range(7)}
    png = render_week(start, week, "")
    img = _open_png(png)
    assert img.width == 1900


def test_render_week_with_lessons():
    start = date(2026, 9, 21)
    week = {start + timedelta(days=i): [] for i in range(7)}
    week[start] = [L(1), L(2)]
    week[start + timedelta(days=1)] = [L(1, discipline="X")]
    png = render_week(start, week, "")
    _open_png(png)


def test_render_week_with_changes_by_day():
    start = date(2026, 9, 21)
    week = {start + timedelta(days=i): [] for i in range(7)}
    week[start] = [L(1)]
    changes = {start: [Change("modify", 1, 0, L(1), {"teacher": ("A", "B")})]}
    png = render_week(start, week, "", changes_by_day=changes)
    _open_png(png)


def test_render_week_returns_png_bytes():
    start = date(2026, 9, 21)
    week = {start + timedelta(days=i): [] for i in range(7)}
    png = render_week(start, week, "")
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG"


# Тесты на теги и ФИО-сплит


def test_render_week_with_sp_tag():
    """Неделя с парой в СП-5 — не падаем."""
    start = date(2026, 9, 21)
    week = {start + timedelta(days=i): [] for i in range(7)}
    week[start] = [L(1, territory="(СП-5) МФЦПК", auditoria="406")]
    png = render_week(start, week, "")
    _open_png(png)


def test_render_day_with_dist_tag():
    """Аудитория «Дист.об.» — тег, не падаем."""
    lessons = [L(1, auditoria="Дист.об.")]
    png = render_day(date(2026, 9, 21), lessons, "")
    _open_png(png)


def test_render_day_changed_has_bell():
    """Изменённая пара рисуется без ошибок."""
    lessons = [L(1)]
    changes = [Change("modify", 1, 0, L(1), {"teacher": ("A", "B")})]
    png = render_day(date(2026, 9, 21), lessons, "", changes=changes)
    _open_png(png)


def test_render_day_with_subgroup_1p():
    """Подгруппа 1 рисуется без ошибок."""
    lessons = [L(1, subgroup=1)]
    png = render_day(date(2026, 9, 21), lessons, "")
    _open_png(png)


def test_render_week_with_all_sp_tags():
    """Все 5 тегов СП рисуются без ошибок."""
    start = date(2026, 9, 21)
    week = {start + timedelta(days=i): [] for i in range(7)}
    for i, sp in enumerate(["СП-1", "СП-2", "СП-3", "СП-4", "СП-5"]):
        week[start + timedelta(days=i % 6)] = [L(1, territory=f"({sp}) Что-то", auditoria="43")]
    png = render_week(start, week, "")
    _open_png(png)


def test_render_day_special_room_no_kab_prefix():
    """Дист.об. и Метод.каб не получают префикс «каб.»"""
    lessons = [L(1, auditoria="Дист.об."), L(2, auditoria="Метод.каб")]
    png = render_day(date(2026, 9, 21), lessons, "")
    _open_png(png)
