"""Рендер расписания в PNG для отправки в VK.

Публичный API:
    render_day(day, lessons, home_territory="", changes=None)          -> bytes
    render_week(week_start, week, home_territory="", changes_by_day=None) -> bytes

Ничего не знает про VK, БД, HTTP — только PIL и данные.

Структура (одинаковая для дня и недели):
    [Пара] [День 1] [День 2] ... [День N]
    номер  ячейки   ячейки       ячейки
    время  ...

Ячейка дня:
    [тег СП / Дист.об. / Метод.каб]     ← если применимо
    Дисциплина (жирная, может переноситься)
    Преподаватель (Фамилия И.О.)
    Тип | каб. XXX

Подгруппы — в одной ячейке, разделены тонкой линией.
Изменённые пары: фон #FFF7E0 + 🔔 перед дисциплиной.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date as date_type, timedelta
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from comparator import Change
from lesson_times import get_lesson_time

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent / "assets"

# ---------- Палитра (по спеке) ----------

COLOR_BG = (255, 255, 255)  # #FFFFFF
COLOR_GRID = (224, 230, 236)  # #E0E6EC
COLOR_HEADER_BG = (248, 251, 254)  # #F8FBFE
COLOR_TEXT = (22, 29, 38)  # #161D26
COLOR_MUTED = (142, 151, 162)  # #8E97A2
COLOR_BLUE = (36, 93, 155)  # #245D9B
COLOR_TIME = (124, 139, 154)  # #7C8B9A
COLOR_CHANGED_BG = (255, 247, 224)  # #FFF7E0
COLOR_SUBGROUP = (255, 115, 25)  # #FF7319 — цвет «1П»/«2П»
COLOR_ROOM_TEXT = (34, 83, 156)  # #22539C

# Цвета тегов СП-X: (bg, fg)
_TAGS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "СП-1": ((216, 225, 237), (34, 83, 156)),  # #D8E1ED / #22539C
    "СП-2": ((249, 228, 214), (255, 101, 1)),  # #F9E4D6 / #FF6501
    "СП-3": ((249, 225, 226), (255, 79, 79)),  # #F9E1E2 / #FF4F4F
    "СП-4": ((229, 248, 226), (83, 210, 62)),  # #E5F8E2 / #53D23E
    "СП-5": ((234, 224, 252), (151, 71, 255)),  # #EAE0FC / #9747FF
}

# Ярлыки аудиторий, для которых «каб.» не пишется
_SPECIAL_ROOM_LABELS = {"дист.об.", "дистант", "дистанционное занятие", "метод.каб", "метод. каб", "метод.кабинет"}

# ---------- Размеры ----------

WEEK_WIDTH = 1900
DAY_WIDTH = 900
PADDING = 25
TIME_COL_WIDTH = 110
HEADER_HEIGHT = 60
ROW_MIN_HEIGHT = 90
CELL_PAD_X = 12
CELL_PAD_Y = 12

# Высоты элементов ячейки
_TAG_H = 24
_DISC_LINE_H = 26
_TEACHER_LINE_H = 22
_INFO_LINE_H = 22
_PLACE_LINE_H = 24
_ROW_GAP = 6
_SUBGROUP_SEP = 18

WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# ---------- Шрифты ----------

_fonts: dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _load_font(name: str, size: int):
    key = f"{name}:{size}"
    if key in _fonts:
        return _fonts[key]

    path = ASSETS_DIR / name
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    if path.exists():
        try:
            font = ImageFont.truetype(str(path), size=size)
        except OSError as e:
            logger.warning("Не удалось открыть шрифт %s: %s — fallback", path, e)
            font = ImageFont.load_default()
    else:
        logger.warning("Шрифт не найден: %s — использую default", path)
        font = ImageFont.load_default()

    _fonts[key] = font
    return font


def _font_regular(size: int):
    return _load_font("NotoSans-Regular.ttf", size)


def _font_bold(size: int):
    return _load_font("NotoSans-Bold.ttf", size)


def _font_emoji(size: int):
    return _load_font("NotoEmoji-Regular.ttf", size)


# ---------- Утилиты ----------


def _shorten_fio(fio: str) -> str:
    """«Кизилова Евгения Александровна» → «Кизилова Е.А.»

    Если уже сокращено («Кизилова Е.А.») — возвращает как есть.
    Если 2 слова — «Дубров Н.». Если 1 — как есть.
    """
    if not fio:
        return "—"
    fio = fio.strip()
    parts = fio.split()
    if len(parts) == 1:
        return parts[0]
    if parts[1].endswith("."):
        return fio
    if len(parts) >= 3:
        return f"{parts[0]} {parts[1][0]}.{parts[2][0]}."
    return f"{parts[0]} {parts[1][0]}."


def _wrap(text: str, font, max_width: int) -> list[str]:
    """Переносит текст по словам так, чтобы каждая строка влезала в max_width."""
    if not text:
        return [""]
    words = text.split()
    lines: list[str] = []
    current = ""
    for w in words:
        candidate = (current + " " + w).strip() if current else w
        try:
            width = font.getlength(candidate)
        except AttributeError:
            # Старые PIL — через bbox
            width = font.getbbox(candidate)[2]
        if width <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines or [""]


def _extract_territory_tag(territory: str) -> Optional[str]:
    """Из «(СП-5) МФЦПК» достаёт «СП-5». None если не найдено."""
    if not territory:
        return None
    m = re.search(r"СП-\d", territory)
    return m.group(0) if m else None


def _format_place_text(lesson: dict) -> str:
    """«каб. 45» / «Дист.об.» / «Метод.каб» / «СР-1».

    Для «специальных» аудиторий префикс «каб.» не пишется.
    """
    aud = (lesson.get("auditoria") or "").strip()
    if not aud:
        return ""
    if aud.lower() in _SPECIAL_ROOM_LABELS or aud.upper().startswith("СР"):
        return aud
    return f"каб. {aud}"


def _has_place_line(lesson: dict) -> bool:
    """Есть ли что рисовать в строке места."""
    return bool(_format_place_text(lesson) or _extract_territory_tag(lesson.get("territory", "")))


# ---------- Измерение высот ----------


def _measure_lesson_block(lesson: dict, avail_width: int) -> int:
    h = 0
    if _has_place_line(lesson):
        h += _PLACE_LINE_H + _ROW_GAP

    disc = lesson.get("discipline", "—")
    disc_lines = _wrap(disc, _font_bold(20), avail_width)
    h += len(disc_lines) * _DISC_LINE_H + _ROW_GAP
    h += _TEACHER_LINE_H + _ROW_GAP
    h += _INFO_LINE_H
    return h


def _measure_cell(lessons: list[dict], avail_width: int) -> int:
    if not lessons:
        return ROW_MIN_HEIGHT
    h = CELL_PAD_Y
    for i, lesson in enumerate(lessons):
        if i > 0:
            h += _SUBGROUP_SEP
        h += _measure_lesson_block(lesson, avail_width)
    h += CELL_PAD_Y
    return max(h, ROW_MIN_HEIGHT)


# ---------- Рисование ----------


def _draw_tag(draw, x: int, y: int, text: str, fg, bg) -> int:
    """Рисует тег, возвращает его ширину. Текст — точно по центру."""
    font = _font_bold(15)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x = 8
    w = int(text_w) + 2 * pad_x
    draw.rectangle([x, y, x + w, y + _TAG_H], fill=bg)
    # Точное центрирование с учётом bbox смещений
    tx = x + (w - text_w) / 2 - bbox[0]
    ty = y + (_TAG_H - text_h) / 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=fg)
    return w


def _draw_place_line(draw, lesson, x, y, font) -> None:
    """Рисует строку места: «каб. 45» + тег «СП-4» справа."""
    place_text = _format_place_text(lesson)
    tag_name = _extract_territory_tag(lesson.get("territory", ""))

    cur_x = x
    if place_text:
        try:
            tw = font.getlength(place_text)
        except AttributeError:
            tw = font.getbbox(place_text)[2]
        draw.text((cur_x, y), place_text, font=font, fill=COLOR_ROOM_TEXT)
        cur_x += int(tw) + 6

    if tag_name and tag_name in _TAGS:
        bg, fg = _TAGS[tag_name]
        _draw_tag(draw, cur_x, y + 2, tag_name, fg, bg)


def _draw_lesson_block(draw, lesson: dict, x: int, y: int, avail_width: int, changed: bool) -> int:
    """Рисует один блок урока. Возвращает нижнюю y-координату."""
    cur_y = y
    cur_x = x

    # 🔔 для изменённых
    if changed:
        draw.text((cur_x, cur_y + 2), "🔔", font=_font_emoji(16), fill=COLOR_TEXT)
        cur_x += 22

    # 1) Место: каб. X + тег СП-X
    if _has_place_line(lesson):
        _draw_place_line(draw, lesson, cur_x, cur_y, _font_regular(16))
        cur_y += _PLACE_LINE_H + _ROW_GAP
        cur_x = x  # далее строки — без сдвига из-за 🔔

    # 2) Дисциплина (жирная, до 2–3 строк)
    disc_lines = _wrap(lesson.get("discipline", "—"), _font_bold(20), avail_width)
    for line in disc_lines:
        draw.text((cur_x, cur_y), line, font=_font_bold(20), fill=COLOR_TEXT)
        cur_y += _DISC_LINE_H
    cur_y += _ROW_GAP

    # 3) ФИО (Фамилия И.О., muted)
    fio = _shorten_fio(lesson.get("teacher", "—"))
    draw.text((x, cur_y), fio, font=_font_regular(17), fill=COLOR_MUTED)
    cur_y += _TEACHER_LINE_H + _ROW_GAP

    # 4) Тип пары + подгруппа
    lesson_type = lesson.get("lesson_type", "—")
    subgroup = lesson.get("subgroup", 0)
    type_font = _font_regular(17)

    draw.text((x, cur_y), lesson_type, font=type_font, fill=COLOR_MUTED)

    if subgroup:
        try:
            tw = type_font.getlength(lesson_type)
        except AttributeError:
            tw = type_font.getbbox(lesson_type)[2]
        subgroup_text = f" {subgroup}П"
        sub_font = _font_bold(17)
        draw.text((x + int(tw), cur_y), subgroup_text, font=sub_font, fill=COLOR_SUBGROUP)

    cur_y += _INFO_LINE_H
    return cur_y


def _draw_day_cell(
    draw, x: int, y: int, w: int, h: int, lessons: list[dict], changes: list[Change], home_territory: str
) -> None:
    changed_keys = {(c.number, c.subgroup) for c in changes}
    cell_changed = any((l["number"], l["subgroup"]) in changed_keys for l in lessons)

    bg = COLOR_CHANGED_BG if cell_changed else COLOR_BG
    draw.rectangle([x, y, x + w, y + h], fill=bg, outline=COLOR_GRID, width=1)

    if not lessons:
        font = _font_regular(20)
        text = "Нет занятий"
        try:
            tw = font.getlength(text)
        except AttributeError:
            tw = font.getbbox(text)[2]
        draw.text((x + (w - tw) / 2, y + h / 2 - 12), text, font=font, fill=COLOR_MUTED)
        return

    avail_width = w - 2 * CELL_PAD_X
    cur_y = y + CELL_PAD_Y

    for i, lesson in enumerate(lessons):
        if i > 0:
            draw.line([x + 8, cur_y, x + w - 8, cur_y], fill=COLOR_GRID, width=1)
            cur_y += _SUBGROUP_SEP

        is_changed = (lesson["number"], lesson["subgroup"]) in changed_keys
        cur_y = _draw_lesson_block(draw, lesson, x + CELL_PAD_X, cur_y, avail_width, is_changed)


def _draw_param_cell(draw, x: int, y: int, w: int, h: int, number: int, time_start: Optional[str]) -> None:
    """Колонка «Пара»: номер + время, выровнены по центру ячейки."""
    draw.rectangle([x, y, x + w, y + h], fill=COLOR_HEADER_BG, outline=COLOR_GRID, width=1)
    num_font = _font_bold(32)
    time_font = _font_regular(16)

    num_text = str(number)

    # Высоты
    try:
        num_bbox = num_font.getbbox(num_text)
        num_h = num_bbox[3] - num_bbox[1]
    except AttributeError:
        num_h = 38
    time_h = 20 if time_start else 0
    gap = 12
    block_h = num_h + (gap + time_h if time_start else 0)
    start_y = y + (h - block_h) // 2 - 4

    # Номер — по центру
    try:
        num_w = num_font.getlength(num_text)
    except AttributeError:
        num_w = num_font.getbbox(num_text)[2]
    draw.text((x + (w - num_w) / 2, start_y), num_text, font=num_font, fill=COLOR_BLUE)

    # Время под номером
    if time_start:
        try:
            time_w = time_font.getlength(time_start)
        except AttributeError:
            time_w = time_font.getbbox(time_start)[2]
        draw.text((x + (w - time_w) / 2, start_y + num_h + gap), time_start, font=time_font, fill=COLOR_TIME)


def _draw_header_cell(
    draw, x: int, y: int, w: int, h: int, day: date_type, text_override: Optional[str] = None
) -> None:
    """Шапка дня. По умолчанию «— 21.09 (Пн) —»."""
    draw.rectangle([x, y, x + w, y + h], fill=COLOR_HEADER_BG, outline=COLOR_GRID, width=1)
    text = text_override or f"— {day.strftime('%d.%m')} ({WEEKDAYS_RU[day.weekday()]}) —"
    font = _font_bold(19)
    try:
        tw = font.getlength(text)
    except AttributeError:
        tw = font.getbbox(text)[2]
    draw.text((x + (w - tw) / 2, y + (h - 24) / 2), text, font=font, fill=COLOR_TEXT)


# ---------- Публичные функции ----------


def render_week(
    week_start: date_type,
    week: dict[date_type, list[dict]],
    home_territory: str = "",
    changes_by_day: Optional[dict[date_type, list[Change]]] = None,
) -> bytes:
    """PNG с расписанием недели (6 колонок, Пн–Сб)."""
    changes_by_day = changes_by_day or {}
    days = [week_start + timedelta(days=i) for i in range(6)]

    width = WEEK_WIDTH
    inner_w = width - 2 * PADDING - TIME_COL_WIDTH
    base_col_w, extra = divmod(inner_w, 6)
    day_widths = [base_col_w + (1 if i < extra else 0) for i in range(6)]

    # Проход 1: высоты строк
    row_heights = []
    for number in range(1, 9):
        max_h = ROW_MIN_HEIGHT
        for i, d in enumerate(days):
            cell_lessons = [l for l in week.get(d, []) if l["number"] == number]
            avail_w = day_widths[i] - 2 * CELL_PAD_X
            max_h = max(max_h, _measure_cell(cell_lessons, avail_w))
        row_heights.append(max_h)

    total_h = PADDING + HEADER_HEIGHT + sum(row_heights) + PADDING

    img = Image.new("RGB", (width, total_h), COLOR_BG)
    draw = ImageDraw.Draw(img)

    # Шапка: колонка «Пара» + 6 дней
    draw.rectangle(
        [PADDING, PADDING, PADDING + TIME_COL_WIDTH, PADDING + HEADER_HEIGHT],
        fill=COLOR_HEADER_BG,
        outline=COLOR_GRID,
        width=1,
    )
    hdr_font = _font_bold(19)
    hdr_text = "Пара"
    try:
        tw = hdr_font.getlength(hdr_text)
    except AttributeError:
        tw = hdr_font.getbbox(hdr_text)[2]
    draw.text(
        (PADDING + (TIME_COL_WIDTH - tw) / 2, PADDING + (HEADER_HEIGHT - 24) / 2),
        hdr_text,
        font=hdr_font,
        fill=COLOR_TEXT,
    )

    x = PADDING + TIME_COL_WIDTH
    for i, d in enumerate(days):
        _draw_header_cell(draw, x, PADDING, day_widths[i], HEADER_HEIGHT, d)
        x += day_widths[i]

    # Строки 1–8
    y = PADDING + HEADER_HEIGHT
    for row_i, number in enumerate(range(1, 9)):
        h = row_heights[row_i]
        time_range = get_lesson_time(1, number)  # эталон — Вт
        time_start = time_range.split("–")[0] if time_range else None
        _draw_param_cell(draw, PADDING, y, TIME_COL_WIDTH, h, number, time_start)

        x = PADDING + TIME_COL_WIDTH
        for i, d in enumerate(days):
            cell_lessons = [l for l in week.get(d, []) if l["number"] == number]
            _draw_day_cell(draw, x, y, day_widths[i], h, cell_lessons, changes_by_day.get(d, []), home_territory)
            x += day_widths[i]

        y += h

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def render_day(
    day: date_type, lessons: list[dict], home_territory: str = "", changes: Optional[list[Change]] = None
) -> bytes:
    """PNG с расписанием одного дня (колонка «Пара» + одна колонка дня)."""
    changes = changes or []

    width = DAY_WIDTH
    day_col_w = width - 2 * PADDING - TIME_COL_WIDTH

    row_heights = []
    for number in range(1, 9):
        cell_lessons = [l for l in lessons if l["number"] == number]
        avail_w = day_col_w - 2 * CELL_PAD_X
        row_heights.append(_measure_cell(cell_lessons, avail_w))

    total_h = PADDING + HEADER_HEIGHT + sum(row_heights) + PADDING

    img = Image.new("RGB", (width, total_h), COLOR_BG)
    draw = ImageDraw.Draw(img)

    # Шапка
    draw.rectangle(
        [PADDING, PADDING, PADDING + TIME_COL_WIDTH, PADDING + HEADER_HEIGHT],
        fill=COLOR_HEADER_BG,
        outline=COLOR_GRID,
        width=1,
    )
    hdr_font = _font_bold(19)
    hdr_text = "Пара"
    try:
        tw = hdr_font.getlength(hdr_text)
    except AttributeError:
        tw = hdr_font.getbbox(hdr_text)[2]
    draw.text(
        (PADDING + (TIME_COL_WIDTH - tw) / 2, PADDING + (HEADER_HEIGHT - 24) / 2),
        hdr_text,
        font=hdr_font,
        fill=COLOR_TEXT,
    )
    _draw_header_cell(draw, PADDING + TIME_COL_WIDTH, PADDING, day_col_w, HEADER_HEIGHT, day)

    # Строки
    y = PADDING + HEADER_HEIGHT
    for row_i, number in enumerate(range(1, 9)):
        h = row_heights[row_i]
        time_range = get_lesson_time(day.weekday(), number)
        time_start = time_range.split("–")[0] if time_range else None
        _draw_param_cell(draw, PADDING, y, TIME_COL_WIDTH, h, number, time_start)

        cell_lessons = [l for l in lessons if l["number"] == number]
        _draw_day_cell(draw, PADDING + TIME_COL_WIDTH, y, day_col_w, h, cell_lessons, changes, home_territory)
        y += h

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
