"""Праздники: загрузка списка из holidays.json.

Публичный API:
    is_holiday(date) -> tuple[bool, str]
    get_holiday(date) -> str | None
    reload() -> None

Файл holidays.json лежит рядом с модулем. Формат:
    {
      "yearly":   [{"date": "MM-DD",   "name": "..."}, ...],
      "specific": [{"date": "YYYY-MM-DD", "name": "..."}, ...]
    }

Если файл отсутствует, пустой или сломан — модуль работает,
но все даты считаются не-праздниками. Логируется warning.
"""

from __future__ import annotations

import json
import logging
from datetime import date as date_type
from pathlib import Path

logger = logging.getLogger(__name__)

HOLIDAYS_PATH = Path(__file__).parent / "holidays.json"

# Кэш: {date: name}. Заполняется при первом обращении или reload().
_cache: dict[date_type, str] | None = None


def _parse_date(value: str, *, yearly: bool) -> date_type | None:
    """Парсит MM-DD (yearly) или YYYY-MM-DD (specific)."""
    try:
        if yearly:
            # MM-DD
            month, day = value.split("-")
            # Год подставляем текущий — при проверке is_holiday
            # используем только месяц и день.
            return (int(month), int(day))  # type: ignore[return-value]
        else:
            # YYYY-MM-DD
            return date_type.fromisoformat(value)
    except (ValueError, AttributeError):
        logger.warning("holidays: некорректная дата %r (yearly=%s)", value, yearly)
        return None


def _load() -> dict[date_type, str]:
    """Загружает и нормализует праздники в dict.

    Yearly-праздники разворачиваются для текущего года.
    Specific — как есть.
    """
    result: dict[date_type, str] = {}

    if not HOLIDAYS_PATH.exists():
        logger.warning("holidays.json не найден: %s", HOLIDAYS_PATH)
        return result

    try:
        data = json.loads(HOLIDAYS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("holidays.json не читается: %s", e)
        return result

    today = date_type.today()

    for item in data.get("yearly", []):
        d = _parse_date(item.get("date", ""), yearly=True)
        if d is None:
            continue
        month, day = d  # type: ignore[misc]
        try:
            real_date = date_type(today.year, month, day)  # type: ignore[arg-type]
            result[real_date] = item.get("name", "")
        except ValueError:
            logger.warning("holidays: невозможно построить дату %02d-%02d", month, day)  # type: ignore[arg-type]

    for item in data.get("specific", []):
        d = _parse_date(item.get("date", ""), yearly=False)
        if d is None:
            continue
        result[d] = item.get("name", "")  # type: ignore[index]

    logger.info("holidays: загружено %d праздников", len(result))
    return result


def _ensure_cache() -> dict[date_type, str]:
    global _cache
    if _cache is None:
        _cache = _load()
    return _cache


def reload() -> None:
    """Перезагружает файл (для тестов и на случай изменений)."""
    global _cache
    _cache = None


def get_holiday(target: date_type) -> str | None:
    """Возвращает имя праздника или None.

    Учитывает yearly (MM-DD) — независимо от года.
    """
    cache = _ensure_cache()

    # Прямая проверка (включая specific)
    if target in cache:
        return cache[target]

    # Fallback: yearly-праздники для целевого года.
    # Если дата 2027-05-09, а кэш построен для 2026 — прямого совпадения нет.
    # Перезагружаем кэш для года target.
    if target.year != date_type.today().year:
        # Достраиваем дату для нужного года
        for d, name in cache.items():
            if d.month == target.month and d.day == target.day:
                return name
    return None


def is_holiday(target: date_type) -> tuple[bool, str]:
    """Возвращает (is_holiday, name). name = "" если не праздник."""
    name = get_holiday(target)
    return (name is not None, name or "")
