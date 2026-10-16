"""Детектор расписаний звонков в сообщениях.

Пока без OCR — работает по тексту (message.text и ocr_text).
OCR будет в фазе 5.

Публичный API:
    detect_bell_schedule(text, ocr_text="", *, today=None) -> BellDetectionResult
    extract_date(text, *, today=None) -> date | None
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date as date_type

logger = logging.getLogger(__name__)


# Триггеры «это сообщение про расписание звонков».
# Ищем как подстроку в нижнем регистре.
_BELL_TRIGGERS = ("звонк", "звонок")  # звонки, звонков, звонкам, звонках

_SOFT_TRIGGERS = ("расписан", "звонк", "звонок")

# Окно поиска даты вокруг триггера (в символах).
_BELL_DATE_WINDOW = 200

# Месяцы в родительном падеже.
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
    """Результат детекта.

    Attributes:
        is_bell_schedule: True, если сообщение похоже на расписание звонков.
        target_date: дата, на которую расписание (или None).
        has_date: True, если дата найдена.
    """

    is_bell_schedule: bool
    target_date: date_type | None
    has_date: bool


def _looks_like_bell(text: str) -> bool:
    """True, если в тексте есть «звонк…» (в любой форме)."""
    if not text:
        return False
    low = text.lower()
    return any(t in low for t in _BELL_TRIGGERS)


def extract_date(text: str, *, today: date_type | None = None) -> date_type | None:
    """Извлекает первую ВАЛИДНУЮ дату из текста.

    Порядок попыток:
      1. DD.MM.YYYY / DD.MM.YY
      2. D месяца YYYY
      3. D месяца (год = текущий)
      4. DD.MM (год = текущий)

    Внутри каждого шага перебираем ВСЕ совпадения: если первая дата
    невалидна (например, 31.02.2026), пробуем следующую.
    """
    if not text:
        return None
    if today is None:
        today = date_type.today()

    text = _DATE_OCR_FIX.sub("1", text)

    # 1) DD.MM.YYYY / DD.MM.YY
    for m in _DATE_DOT_RE.finditer(text):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return date_type(year, month, day)
        except ValueError:
            continue

    # 2, 3) D месяца [YYYY]
    for m in _DATE_WORD_RE.finditer(text):
        day = int(m.group(1))
        month = _MONTHS_RU[m.group(2).lower()]
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            return date_type(year, month, day)
        except ValueError:
            continue

    # 4) DD.MM
    for m in _DATE_DOT_SHORT_RE.finditer(text):
        day, month = int(m.group(1)), int(m.group(2))
        try:
            return date_type(today.year, month, day)
        except ValueError:
            continue

    return None


def _find_trigger_position(text: str) -> int | None:
    """Возвращает индекс первого вхождения триггера «звонк…»."""
    low = text.lower()
    best = None
    for trig in _BELL_TRIGGERS:
        pos = low.find(trig)
        if pos >= 0 and (best is None or pos < best):
            best = pos
    return best


def _find_date_near_bell(text: str, today: date_type) -> date_type | None:
    """Ищет дату рядом с «звонк».

    Приоритет:
      1. Дата в окне +200 символов ПОСЛЕ триггера (это целевая дата).
      2. Дата в окне -200 символов ДО триггера (шапка документа).
      3. Дата в любом месте текста (fallback).
    """
    idx = _find_trigger_position(text)
    if idx is None:
        return None

    # 1) После триггера
    after = text[idx : min(len(text), idx + _BELL_DATE_WINDOW)]
    dt = extract_date(after, today=today)
    if dt is not None:
        return dt

    # 2) До триггера
    before = text[max(0, idx - _BELL_DATE_WINDOW) : idx]
    dt = extract_date(before, today=today)
    if dt is not None:
        return dt

    # 3) Fallback — весь текст
    return extract_date(text, today=today)


def detect_bell_schedule(text: str, ocr_text: str = "", *, today: date_type | None = None) -> BellDetectionResult:
    """Определяет, является ли сообщение расписанием звонков.

    Args:
        text: текст сообщения (то, что напечатал пользователь).
        ocr_text: распознанный с картинки текст.
        today: для unit-тестов; по умолчанию — date.today().

    Returns:
        BellDetectionResult:
          is_bell_schedule=True только если найдены и триггер, и дата.
    """
    if today is None:
        today = date_type.today()

    text = text or ""
    ocr_text = ocr_text or ""
    combined = f"{text}\n\n{ocr_text}".strip()
    if not combined:
        return BellDetectionResult(False, None, False)

    if not _looks_like_bell(combined):
        return BellDetectionResult(False, None, False)

    # Дата. Приоритет — текст от пользователя.
    # Там OCR не может ошибиться (17 vs 07, 1 vs 0).
    dt: date_type | None = None
    if text and _looks_like_schedule(text):
        dt = extract_date(text, today=today)

    # Fallback на OCR с окном ±200 вокруг «звонк»
    if dt is None and ocr_text:
        dt = _find_date_near_bell(ocr_text, today)

    # Последняя попытка — общий поиск
    if dt is None:
        dt = _find_date_near_bell(combined, today)

    if dt is None:
        return BellDetectionResult(False, None, False)
    return BellDetectionResult(True, dt, True)


def _looks_like_schedule(text: str) -> bool:
    """True, если в тексте есть «расписан…», «звонк…» (мягкий триггер)."""
    if not text:
        return False
    low = text.lower()
    return any(t in low for t in _SOFT_TRIGGERS)
