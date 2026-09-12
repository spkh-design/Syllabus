"""Планировщик проверок расписания.

Решает, КОГДА и ЧТО проверять, вызывает API, сравнивает снимки
и возвращает готовые сообщения для ВК.

Публичный API:
    should_check_now(now)                  -> set[date]
    refresh_base_info_if_needed(conn, base) -> dict
    check_group(conn, base, group_uuid, date) -> Optional[tuple[str, str]]
    run_check_cycle(conn, base, group_uuid)  -> list[str]  # готовые тексты
"""

from __future__ import annotations

import logging
from datetime import date as date_type, datetime, time, timedelta
from typing import Optional

import database as db
import schedule_api
from comparator import (
    diff_lessons,
    is_massive_change,
)
from formatter import (
    format_changes,
    format_day_changes_full,
    split_message,
)

logger = logging.getLogger(__name__)

# Срок, после которого справочники считаются устаревшими
BASE_INFO_TTL_HOURS = 24


# ---------- Логика времени ----------

def _in_time_window(
    now: time, start: time, end: time,
) -> bool:
    """Проверяет, попадает ли now в [start, end]."""
    return start <= now <= end


def _next_monday(d: date_type) -> date_type:
    """Ближайший понедельник СТРОГО после d.

    Если d — воскресенье, вернёт следующий день (Пн).
    Если d — понедельник, вернёт через 7 дней.
    """
    days_ahead = (0 - d.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return d + timedelta(days=days_ahead)


def _next_week_dates(d: date_type) -> set[date_type]:
    """Все 7 дней следующей недели (Пн–Вс)."""
    monday = _next_monday(d)
    return {monday + timedelta(days=i) for i in range(7)}


def should_check_now(now: datetime) -> set[date_type]:
    """Определяет, какие даты нужно проверить в этот момент.

    Правила:
      Пн–Пт 06:30–17:00  -> сегодня
      Пн–Пт 14:30–23:00  -> завтра
      Пт    14:00–23:00  -> вся следующая неделя
      Сб    08:00–23:00  -> вся следующая неделя
      Вс    08:00–23:00  -> вся следующая неделя
      Вс    14:30–23:00  -> завтра (= Пн)

    Returns:
        Множество дат. Пустое — если сейчас ничего проверять не надо.
    """
    today = now.date()
    wd = today.weekday()  # 0=Пн, ..., 6=Вс
    t = now.time()

    dates: set[date_type] = set()

    if 0 <= wd <= 4:  # Пн–Пт
        # Сегодня
        if _in_time_window(t, time(6, 30), time(17, 0)):
            dates.add(today)

        # Завтра
        if _in_time_window(t, time(14, 30), time(23, 0)):
            dates.add(today + timedelta(days=1))

        # Пт: вся следующая неделя с 14:00
        if wd == 4 and _in_time_window(t, time(14, 0), time(23, 0)):
            dates |= _next_week_dates(today)

    elif wd == 5:  # Сб
        if _in_time_window(t, time(8, 0), time(23, 0)):
            dates |= _next_week_dates(today)

    elif wd == 6:  # Вс
        # Вся следующая неделя
        if _in_time_window(t, time(8, 0), time(23, 0)):
            dates |= _next_week_dates(today)

        # Завтра (= Пн)
        if _in_time_window(t, time(14, 30), time(23, 0)):
            dates.add(today + timedelta(days=1))

    return dates


# ---------- Справочники ----------

def refresh_base_info_if_needed(
    conn, current_base: Optional[dict] = None,
) -> Optional[dict]:
    """Обновляет справочники, если они устарели или их нет.

    Returns:
        Актуальные справочники или None, если обновить не удалось
        и в кэше тоже пусто.
    """
    age = db.base_info_age_hours(conn)
    if current_base is not None and age is not None and age < BASE_INFO_TTL_HOURS:
        return current_base

    try:
        fresh = schedule_api.fetch_base_info()
    except schedule_api.ScheduleAPIError as e:
        logger.warning("Не удалось обновить справочники: %s", e)
        return current_base or db.get_base_info(conn)

    db.save_base_info(conn, fresh)
    logger.info("Справочники обновлены")
    return fresh


# ---------- Проверка одной даты ----------
def _increment_metric(conn, key: str, value=None) -> None:
    """Инкрементирует счётчик (или устанавливает значение).

    Если value=None — увеличивает на 1.
    Если value задан — устанавливает его как значение.
    """
    if value is not None:
        db.set_setting(conn, key, value)
        return
    current = db.get_setting(conn, key, 0) or 0
    db.set_setting(conn, key, current + 1)


def check_group(
    conn, base: dict, group_uuid: str, target_date: date_type,
) -> Optional[tuple[str, str]]:
    """Проверяет одну дату для группы. Возвращает (kind, text) или None.

    kind:  'diff' или 'full' — тип уведомления.
    text:  готовый текст для отправки.

    Побочные эффекты:
      - обновляет снимок в БД;
      - пишет в notifications_log при наличии изменений.

    Возвращает None, если:
      - изменений нет;
      - снимка не было и новых данных тоже нет (первый запуск без расписания).
    """
    try:
        new_lessons = schedule_api.get_group_lessons(base, group_uuid, target_date)
    except schedule_api.ScheduleAPIError as e:
        logger.warning("API-ошибка на %s: %s", target_date, e)
        _increment_metric(conn, "metrics_errors")
        return None

    old_snapshot = db.get_snapshot(conn, group_uuid, target_date)

    # Первый запуск: снимка не было. Просто сохраняем — не уведомляем.
    # Иначе при развёртывании бота каждый подписчик получит полное расписание.
    if old_snapshot is None:
        if new_lessons:
            db.save_snapshot(conn, group_uuid, target_date, new_lessons)
            logger.info("Первичный снимок %s на %s сохранён",
                        group_uuid, target_date)
        return None

    old_lessons = old_snapshot["lessons"]

    # Изменений нет — ничего не делаем (даже не пересохраняем).
    if old_snapshot["hash"] == _hash(new_lessons):
        return None

    changes = diff_lessons(old_lessons, new_lessons)

    # Снимок изменился, но diff пустой (крайне редкий случай, например
    # изменился порядок lessons в API). Просто сохраняем новый снимок.
    if not changes:
        db.save_snapshot(conn, group_uuid, target_date, new_lessons)
        return None

    # Сохраняем новый снимок до отправки — если отправка упадёт,
    # не будем повторять уведомление.
    db.save_snapshot(conn, group_uuid, target_date, new_lessons)

    # Определяем, слать полное или короткое
    total = max(len(old_lessons), len(new_lessons), 1)
    home = schedule_api.home_territory(base, group_uuid)
    if is_massive_change(changes, total):
        text = format_day_changes_full(target_date, changes, new_lessons, home)
        kind = "full"
    else:
        text = format_changes(target_date, changes, new_lessons)
        kind = "diff"

    db.log_notification(conn, group_uuid, target_date, kind, text[:500])

    return kind, text


def _hash(lessons: list[dict]) -> str:
    """Стабильный хеш (совпадает с тем, что кладёт database.save_snapshot)."""
    import hashlib, json
    normalized = json.dumps(lessons, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---------- Полный цикл ----------

def run_check_cycle(
    conn, base: Optional[dict], group_uuid: str, now: Optional[datetime] = None,
) -> list[str]:
    """Полный цикл: определить, что проверять, проверить, вернуть тексты.

    Args:
        conn:       соединение с БД.
        base:       справочники (если None — попробует загрузить из БД).
        group_uuid: UUID отслеживаемой группы.
        now:        текущее время (для тестов). Если None — берётся datetime.now().

    Returns:
        Список готовых текстов сообщений для отправки в ВК.
        Каждый текст уже нарезан по лимиту (split_message).
    """
    if now is None:
        now = datetime.now()

    if base is None:
        base = db.get_base_info(conn)
    if base is None:
        logger.error("Нет справочников — пропуск цикла")
        return []

    dates = should_check_now(now)
    if not dates:
        logger.debug("Сейчас ничего проверять не нужно")
        return []

    logger.info("Проверяем %d дат(ы): %s",
                len(dates), sorted(d.isoformat() for d in dates))

    messages: list[str] = []
    for d in sorted(dates):
        result = check_group(conn, base, group_uuid, d)
        if result is None:
            continue
        kind, text = result
        logger.info("Изменения на %s (%s)", d, kind)
        messages.extend(split_message(text))

    _increment_metric(conn, "metrics_checks")
    _increment_metric(conn, "metrics_last_check", value=datetime.now().isoformat())
    for _ in messages:
        _increment_metric(conn, "metrics_notifications")
        
    return messages