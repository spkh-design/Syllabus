"""Детектор расписаний звонков в сообщениях."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date as date_type

logger = logging.getLogger(__name__)

# Триггеры «это сообщение про расписание звонков».
_BELL_TRIGGERS = ("звонк", "звонок")
_TEXT_TRIGGERS = ("пары по часу", "без обеда", "сокращенный день", "сокращенные пары")

_SOFT_TRIGGERS = ("расписан", "звонк", "звонок")

_BELL_DATE_WINDOW = 200

_MONTHS_RU = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

_DATE_DOT_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{2,4})(?!\d)")
_DATE_DOT_SHORT_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})(?!\d)")
_DATE_WORD_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s+(" + "|".join(_MONTHS_RU.keys()) + r")(?:\s+(\d{4}))?(?!\d)", re.IGNORECASE
)
_DATE_OCR_FIX = re.compile(r"[\[\]|Il!](?=\d\.)")


@dataclass
class BellDetectionResult:
    is_bell_schedule: bool
    target_date: date_type | None
    has_date: bool
    caption: str = ""  # <--- ДОБАВЛЕНО ПОЛЕ ДЛЯ ТЕКСТА


def _looks_like_bell(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(t in low for t in _BELL_TRIGGERS) or any(t in low for t in _TEXT_TRIGGERS)


def _looks_like_schedule(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(t in low for t in _SOFT_TRIGGERS) or any(t in low for t in _TEXT_TRIGGERS)


def extract_date(text: str, *, today: date_type | None = None) -> date_type | None:
    if not text:
        return None
    if today is None:
        today = date_type.today()

    text = _DATE_OCR_FIX.sub("1", text)

    for m in _DATE_DOT_RE.finditer(text):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return date_type(year, month, day)
        except ValueError:
            continue

    for m in _DATE_WORD_RE.finditer(text):
        day = int(m.group(1))
        month = _MONTHS_RU[m.group(2).lower()]
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            return date_type(year, month, day)
        except ValueError:
            continue

    for m in _DATE_DOT_SHORT_RE.finditer(text):
        day, month = int(m.group(1)), int(m.group(2))
        try:
            return date_type(today.year, month, day)
        except ValueError:
            continue

    return None


def _extract_date_and_caption(chunk: str, today: date_type) -> tuple[date_type | None, str]:
    """Извлекает дату и оставшийся текст."""
    dt = extract_date(chunk, today=today)
    if not dt:
        return None, ""

    # Удаляем дату из строки, чтобы получить чистый текст
    # 1. DD.MM.YYYY или DD.MM
    m = re.search(r"\b\d{1,2}\.\d{1,2}(\.\d{2,4})?\b", chunk)
    if m:
        caption = chunk[: m.start()] + chunk[m.end() :]
    else:
        # 2. D месяца
        m = re.search(
            r"\b\d{1,2}\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)(\s+\d{4})?\b",
            chunk,
            re.IGNORECASE,
        )
        if m:
            caption = chunk[: m.start()] + chunk[m.end() :]
        else:
            caption = chunk

    caption = caption.strip(" -—:,.!")
    return dt, caption


def _detect_multiple_from_text(text: str, today: date_type) -> list[BellDetectionResult]:
    """Ищет список дат и текста в одном сообщении."""
    if not text:
        return []

    low = text.lower()
    if not any(t in low for t in _BELL_TRIGGERS) and not any(t in low for t in _TEXT_TRIGGERS):
        return []

    # Разбиваем по переносам строк, если нет — по запятым
    if "\n" in text:
        chunks = text.split("\n")
    else:
        chunks = text.split(",")

    results = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        # Игнорируем выходные
        if "выходной" in chunk.lower() or "выходные" in chunk.lower():
            continue

        dt, caption = _extract_date_and_caption(chunk, today)
        if dt:
            if not caption:
                caption = "расписание звонков"
            results.append(BellDetectionResult(True, dt, True, caption))

    return results


def _find_trigger_position(text: str) -> int | None:
    low = text.lower()
    best = None
    for trig in _BELL_TRIGGERS + _TEXT_TRIGGERS:
        pos = low.find(trig)
        if pos >= 0 and (best is None or pos < best):
            best = pos
    return best


def _find_date_near_bell(text: str, today: date_type) -> date_type | None:
    idx = _find_trigger_position(text)
    if idx is None:
        return None

    after = text[idx : min(len(text), idx + _BELL_DATE_WINDOW)]
    dt = extract_date(after, today=today)
    if dt is not None:
        return dt

    before = text[max(0, idx - _BELL_DATE_WINDOW) : idx]
    dt = extract_date(before, today=today)
    if dt is not None:
        return dt

    return extract_date(text, today=today)


def detect_bell_schedules(
    text: str, ocr_text: str = "", *, today: date_type | None = None
) -> list[BellDetectionResult]:
    """Основная точка входа. Возвращает список найденных расписаний."""
    if today is None:
        today = date_type.today()

    text = text or ""
    ocr_text = ocr_text or ""

    # 1. Множественный поиск в тексте
    multi_results = _detect_multiple_from_text(text, today)
    if multi_results:
        return multi_results

    # 2. Старая логика (одиночное расписание, OCR)
    combined = f"{text}\n\n{ocr_text}".strip()
    if not combined:
        return []

    if not _looks_like_bell(combined):
        return []

    dt: date_type | None = None
    if text and _looks_like_schedule(text):
        dt = extract_date(text, today=today)

    if dt is None and ocr_text:
        dt = _find_date_near_bell(ocr_text, today)

    if dt is None:
        dt = _find_date_near_bell(combined, today)

    if dt is None:
        return []

    return [BellDetectionResult(True, dt, True, "расписание звонков")]


# Оставляем старую функцию для совместимости с тестами
def detect_bell_schedule(text: str, ocr_text: str = "", *, today: date_type | None = None) -> BellDetectionResult:
    results = detect_bell_schedules(text, ocr_text, today=today)
    if results:
        return results[0]
    return BellDetectionResult(False, None, False)
