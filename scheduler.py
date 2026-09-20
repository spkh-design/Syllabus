"""Планировщик проверок расписания.

Решает, КОГДА и ЧТО проверять, вызывает API, сравнивает снимки
и возвращает готовые сообщения для ВК.

Публичный API:
    should_check_now(now)                       -> set[date]
    refresh_base_info_if_needed(conn, base)     -> dict
    check_group(conn, base, group_uuid, date, ...) -> Optional[(kind, text, changes)]
    run_check_cycle(conn, base, group_uuid)     -> list[str]
"""

from __future__ import annotations

import logging
from datetime import date as date_type, datetime, time, timedelta
from typing import Optional
from dataclasses import dataclass, field
import database as db
import schedule_api
from comparator import Change, count_changed_days, diff_lessons, is_massive_change
from formatter import (
    format_changes,
    format_day_changes_full,
    format_week_changes_full,
    format_week_schedule,
    split_message,
)
from lesson_times import get_lesson_time

logger = logging.getLogger(__name__)

# Срок, после которого справочники считаются устаревшими
BASE_INFO_TTL_HOURS = 24

# Минимальное число дней с парами, чтобы неделя считалась «полной»
WEEK_COMPLETE_MIN_DAYS = 3

# Время, после которого в воскресенье можно слать неполную неделю
SUNDAY_INCOMPLETE_SEND_TIME = time(13, 0)

# Месяцы/дни, с которых начинается первая неделя семестра
SEMESTER_STARTS = ((9, 1), (1, 8))

# Сколько дней длится «первая неделя семестра»
SEMESTER_FIRST_WEEK_DAYS = 7

# Порог «3+ дня → вся неделя»
MASSIVE_DAYS_FOR_WEEK = 3


@dataclass
class ScheduleMessage:
    """Готовое к отправке сообщение с расписанием.

    Attributes:
        kind:           'day_image' | 'week_image' | 'text'
        text:           короткая подпись (уходит в message вместе с вложением)
        fallback_text:  текст на случай, если картинка/upload не удались
        target_date:    для day_image — день
        week_start:     для week_image — понедельник недели
        changes:        список Change (для day_image)
        changes_by_day: {дата: [Change]} (для week_image)
        lessons:        пары на день (для day_image)
        week:           {дата: [пары]} (для week_image)
        home_territory: название подразделения группы
        png_bytes:      если картинка уже отрендерена
    """

    kind: str
    text: str = ""
    fallback_text: str = ""
    target_date: Optional[date_type] = None
    week_start: Optional[date_type] = None
    changes: list[Change] = field(default_factory=list)
    changes_by_day: dict[date_type, list[Change]] = field(default_factory=dict)
    lessons: Optional[list[dict]] = None
    week: Optional[dict[date_type, list[dict]]] = None
    home_territory: str = ""
    png_bytes: Optional[bytes] = None


# ---------- Логика времени ----------


def _in_time_window(now: time, start: time, end: time) -> bool:
    """Проверяет, попадает ли now в [start, end]."""
    return start <= now <= end


def _current_monday(d: date_type) -> date_type:
    """Понедельник недели, в которую входит d."""
    return d - timedelta(days=d.weekday())


def _next_monday(d: date_type) -> date_type:
    """Ближайший понедельник СТРОГО после d."""
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
    """
    today = now.date()
    wd = today.weekday()
    t = now.time()

    dates: set[date_type] = set()

    if 0 <= wd <= 4:  # Пн–Пт
        if _in_time_window(t, time(6, 30), time(17, 0)):
            dates.add(today)
        if _in_time_window(t, time(14, 30), time(23, 0)):
            dates.add(today + timedelta(days=1))
        if wd == 4 and _in_time_window(t, time(14, 0), time(23, 0)):
            dates |= _next_week_dates(today)

    elif wd == 5:  # Сб
        if _in_time_window(t, time(8, 0), time(23, 0)):
            dates |= _next_week_dates(today)

    elif wd == 6:  # Вс
        if _in_time_window(t, time(8, 0), time(23, 0)):
            dates |= _next_week_dates(today)
        if _in_time_window(t, time(14, 30), time(23, 0)):
            dates.add(today + timedelta(days=1))

    return dates


def _is_sunday_after_13(now: datetime) -> bool:
    """True, если сейчас воскресенье и время >= 13:00."""
    return now.date().weekday() == 6 and now.time() >= SUNDAY_INCOMPLETE_SEND_TIME


def _is_first_week_of_semester(today: date_type) -> bool:
    """True, если today — в пределах 7 дней от начала семестра."""
    for month, day in SEMESTER_STARTS:
        try:
            start = date_type(today.year, month, day)
        except ValueError:
            continue
        end = start + timedelta(days=SEMESTER_FIRST_WEEK_DAYS - 1)
        if start <= today <= end:
            return True
    return False


def _split_dates_by_week(dates: set[date_type], today: date_type) -> tuple[set[date_type], set[date_type]]:
    """Разделяет даты на «текущая неделя» и «следующая неделя».

    Returns:
        (current_week_dates, next_week_dates)
    """
    current_mon = _current_monday(today)
    current_week = {current_mon + timedelta(days=i) for i in range(7)}

    next_mon = current_mon + timedelta(days=7)
    next_week = {next_mon + timedelta(days=i) for i in range(7)}

    current = dates & current_week
    future = dates & next_week
    return current, future


def _is_week_complete(week: dict[date_type, list[dict]]) -> bool:
    """True, если в неделе >= WEEK_COMPLETE_MIN_DAYS дней с парами."""
    days_with_lessons = sum(1 for lessons in week.values() if lessons)
    return days_with_lessons >= WEEK_COMPLETE_MIN_DAYS


def _day_lessons_passed(day: date_type, last_lesson_number: int, now: datetime) -> bool:
    """True, если все пары дня уже прошли."""
    if last_lesson_number <= 0:
        return True
    lesson_time_range = get_lesson_time(day.weekday(), last_lesson_number)
    if lesson_time_range is None:
        return True
    _, end_str = lesson_time_range.split("–")
    end_h, end_m = map(int, end_str.split(":"))
    return now.time() > time(end_h, end_m)


# ---------- Справочники ----------


def refresh_base_info_if_needed(conn, current_base: Optional[dict] = None) -> Optional[dict]:
    """Обновляет справочники, если они устарели или их нет."""
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


# ---------- Метрики ----------


def _increment_metric(conn, key: str, value=None) -> None:
    """Инкрементирует счётчик (или устанавливает значение)."""
    if value is not None:
        db.set_setting(conn, key, value)
        return
    current = db.get_setting(conn, key, 0) or 0
    db.set_setting(conn, key, current + 1)


# ---------- Проверка одной даты ----------


def check_group(
    conn, base, group_uuid, target_date, *, now=None, skip_if_passed_today=True
) -> Optional[ScheduleMessage]:
    try:
        new_lessons = schedule_api.get_group_lessons(base, group_uuid, target_date)
    except schedule_api.ScheduleAPIError as e:
        logger.warning("API-ошибка на %s: %s", target_date, e)
        _increment_metric(conn, "metrics_errors")
        return None

    old_snapshot = db.get_snapshot(conn, group_uuid, target_date)
    if old_snapshot is None:
        if new_lessons:
            db.save_snapshot(conn, group_uuid, target_date, new_lessons)
        return None

    old_lessons = old_snapshot["lessons"]
    if old_snapshot["hash"] == _hash(new_lessons):
        return None

    changes = diff_lessons(old_lessons, new_lessons)
    if not changes:
        db.save_snapshot(conn, group_uuid, target_date, new_lessons)
        return None

    db.save_snapshot(conn, group_uuid, target_date, new_lessons)

    if skip_if_passed_today and now is not None and target_date == now.date():
        last_number = max((l["number"] for l in new_lessons), default=0)
        if _day_lessons_passed(target_date, last_number, now):
            db.log_notification(conn, group_uuid, target_date, "skipped_passed", "")
            return None

    total = max(len(old_lessons), len(new_lessons), 1)
    home = schedule_api.home_territory(base, group_uuid)

    # kind = full → day_image (полное расписание дня с маркерами)
    # kind = diff → тоже day_image, но маркеров меньше
    # В обоих случаях шлём картинку дня.
    fallback_text = (
        format_day_changes_full(target_date, changes, new_lessons, home)
        if is_massive_change(changes, total)
        else format_changes(target_date, changes, new_lessons)
    )
    # Заголовок «⚠️ Изменения на 21.09 (Пн)» — подпись под картинку
    weekday = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][target_date.weekday()]
    text = f"⚠️ Изменения на {target_date.strftime('%d.%m')} ({weekday})"

    kind = "full" if is_massive_change(changes, total) else "diff"
    db.log_notification(conn, group_uuid, target_date, kind, fallback_text[:500])

    return ScheduleMessage(
        kind="day_image",
        text=text,
        fallback_text=fallback_text,
        target_date=target_date,
        changes=changes,
        lessons=new_lessons,
        home_territory=home,
    )


def _hash(lessons: list[dict]) -> str:
    """Стабильный хеш (совпадает с database.save_snapshot)."""
    import hashlib, json

    normalized = json.dumps(lessons, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---------- Объявление недели ----------


def _maybe_announce_week(conn, base, group_uuid, week_start, week, now):
    existing = db.get_week_announced(conn, group_uuid, week_start)
    if existing is not None:
        return None

    if _is_first_week_of_semester(now.date()):
        return None

    complete = _is_week_complete(week)
    is_sunday = _is_sunday_after_13(now)

    if not complete and not is_sunday:
        return None

    home = schedule_api.home_territory(base, group_uuid)
    fallback = format_week_schedule(week_start, week, home)
    text = (
        f"📅 Расписание на неделю "
        f"с {week_start.strftime('%d.%m.%Y')} "
        f"по {(week_start + timedelta(days=5)).strftime('%d.%m.%Y')}"
    )

    db.set_week_announced(conn, group_uuid, week_start, is_full=complete)
    kind = "week_full" if complete else "week_partial"
    db.log_notification(conn, group_uuid, week_start, kind, fallback[:500])

    return ScheduleMessage(
        kind="week_image", text=text, fallback_text=fallback, week_start=week_start, week=week, home_territory=home
    )


# ---------- Полный цикл ----------


def run_check_cycle(
    conn, base: Optional[dict], group_uuid: str, now: Optional[datetime] = None
) -> list[ScheduleMessage]:
    """Полный цикл: определить, что проверять, проверить, вернуть ScheduleMessage.

    Алгоритм:
      1. should_check_now → set[date].
      2. Разделяем на текущую и следующую недели.
      3. Для следующей недели: загружаем расписание, проверяем
         объявление. Если объявили — добавляем ScheduleMessage.
      4. Для каждой даты: check_group. Собираем changes_by_day и day_messages.
      5. Для каждой недели: если count_changed_days >= 3 →
         одно ScheduleMessage(kind='week_image'). Иначе — по одному
         ScheduleMessage(kind='day_image') на изменившийся день.
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

    logger.info("Проверяем %d дат(ы): %s", len(dates), sorted(d.isoformat() for d in dates))

    today = now.date()
    current_dates, next_dates = _split_dates_by_week(dates, today)

    messages: list[ScheduleMessage] = []
    week_cache: dict[date_type, dict[date_type, list[dict]]] = {}

    # --- 1. Следующая неделя: объявление ---
    if next_dates:
        week_start = min(next_dates)
        week = schedule_api.get_week_lessons(base, group_uuid, week_start)
        week_cache[week_start] = week

        announce_msg = _maybe_announce_week(conn, base, group_uuid, week_start, week, now)
        if announce_msg is not None:
            messages.append(announce_msg)

    # --- 2. Проверяем каждую дату, собираем изменения ---
    changes_by_day: dict[date_type, list[Change]] = {}
    day_messages: dict[date_type, ScheduleMessage] = {}

    for d in sorted(current_dates | next_dates):
        result = check_group(conn, base, group_uuid, d, now=now, skip_if_passed_today=True)
        if result is None:
            continue
        logger.info("Изменения на %s (%s, %d изменений)", d, result.kind, len(result.changes))
        changes_by_day[d] = result.changes
        day_messages[d] = result

    # --- 3. Группируем по неделям и решаем, что отправлять ---
    _process_week_messages(messages, changes_by_day, day_messages, current_dates, base, group_uuid)
    _process_week_messages(
        messages,
        changes_by_day,
        day_messages,
        next_dates,
        base,
        group_uuid,
        week_start=(min(next_dates) if next_dates else None),
        week_cache=week_cache,
    )

    _increment_metric(conn, "metrics_checks")
    _increment_metric(conn, "metrics_last_check", value=datetime.now().isoformat())
    for _ in messages:
        _increment_metric(conn, "metrics_notifications")

    return messages


def _process_week_messages(
    messages: list[ScheduleMessage],
    changes_by_day: dict[date_type, list[Change]],
    day_messages: dict[date_type, ScheduleMessage],
    week_dates: set[date_type],
    base: dict,
    group_uuid: str,
    *,
    week_start: Optional[date_type] = None,
    week_cache: Optional[dict[date_type, dict[date_type, list[dict]]]] = None,
) -> None:
    """Формирует ScheduleMessage для недели.

    Если >= MASSIVE_DAYS_FOR_WEEK дней недели изменились — одно
    ScheduleMessage(kind='week_image'). Иначе — по одному
    ScheduleMessage(kind='day_image') на изменившийся день.
    """
    relevant_changes = {d: c for d, c in changes_by_day.items() if d in week_dates}
    if not relevant_changes:
        return

    if count_changed_days(relevant_changes) >= MASSIVE_DAYS_FOR_WEEK:
        # Одна недельная картинка
        start = week_start or min(week_dates)
        week = (week_cache or {}).get(start)
        if week is None:
            week = schedule_api.get_week_lessons(base, group_uuid, start)

        home = schedule_api.home_territory(base, group_uuid)
        fallback = format_week_changes_full(start, relevant_changes, week, home)
        text = (
            f"⚠️ Изменения за неделю " f"{start.strftime('%d.%m')}–" f"{(start + timedelta(days=5)).strftime('%d.%m')}"
        )
        messages.append(
            ScheduleMessage(
                kind="week_image",
                text=text,
                fallback_text=fallback,
                week_start=start,
                changes_by_day=relevant_changes,
                week=week,
                home_territory=home,
            )
        )
        return

    # По одному дню — берём уже готовые ScheduleMessage из day_messages
    for d in sorted(relevant_changes):
        msg = day_messages.get(d)
        if msg is not None:
            messages.append(msg)
