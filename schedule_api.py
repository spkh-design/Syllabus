"""Клиент API колледжа СПК.

Единственный модуль, который знает про HTTP, URL и структуру JSON.
Все остальные модули работают с уже нормализованными данными.

Публичные функции:
    fetch_base_info()      -> dict   # справочники
    fetch_schedule(date)   -> dict   # сырое расписание на дату
    build_lookup(base)     -> dict   # UUID -> название
    find_group(base, name, division_part) -> dict | None
    get_group_lessons(base, group_uuid, date) -> list[dict]
    get_week_lessons(base, group_uuid, start_date) -> dict[date, list[dict]]
"""

from __future__ import annotations

import logging
import time
import re
from datetime import date as date_type, datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------- Константы ----------

BASE_URL = "https://surpk.ru"
INDEX_ENDPOINT = f"{BASE_URL}/api/schedule/index"
SCHEDULE_ENDPOINT = f"{BASE_URL}/api/schedule/schedule"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://schedule.surpk.ru",
    "Referer": "https://schedule.surpk.ru/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
    ),
}

TIMEOUT = 30  # секунд на один запрос
MAX_RETRIES = 3  # попыток при 429/503/сетевых ошибках
BACKOFF_BASE = 2  # 2, 4, 8 секунд между попытками


class ScheduleAPIError(Exception):
    """Общая ошибка обращения к API колледжа."""


class ScheduleNotFoundError(ScheduleAPIError):
    """Данных на запрошенную дату нет."""


# ---------- HTTP-обёртка с ретраями ----------


def _request(url: str, params: Optional[dict] = None) -> dict:
    """GET-запрос с ретраями. Возвращает распарсенный JSON.

    Ретраи — при сетевых ошибках и при 429/500/502/503/504.
    Экспоненциальная задержка: 2с, 4с, 8с.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            last_exc = e
            logger.warning("Сетевая ошибка (попытка %d): %s", attempt + 1, e)
        else:
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as e:
                    raise ScheduleAPIError(f"Ответ не является JSON: {e}") from e

            if response.status_code in (429, 500, 502, 503, 504):
                last_exc = ScheduleAPIError(f"HTTP {response.status_code}")
                logger.warning("HTTP %d (попытка %d)", response.status_code, attempt + 1)
            else:
                raise ScheduleAPIError(f"HTTP {response.status_code}: {response.text[:200]}")

        if attempt < MAX_RETRIES - 1:
            delay = BACKOFF_BASE ** (attempt + 1)
            time.sleep(delay)

    raise ScheduleAPIError(f"Не удалось получить данные: {last_exc}")


# ---------- Публичные функции ----------


def fetch_base_info() -> dict:
    """Загружает справочники. Возвращает data из baseInfo."""
    payload = _request(INDEX_ENDPOINT)
    if not payload.get("success"):
        raise ScheduleAPIError("index: success=false")
    try:
        return payload["baseInfo"]["data"]
    except (KeyError, TypeError) as e:
        raise ScheduleAPIError(f"index: неожиданная структура: {e}") from e


def fetch_schedule(target_date: date_type) -> dict:
    """Загружает сырое расписание на дату."""
    ts_ms = int(datetime(target_date.year, target_date.month, target_date.day).timestamp() * 1000)
    payload = _request(SCHEDULE_ENDPOINT, params={"date": ts_ms})
    if not payload.get("success"):
        raise ScheduleAPIError(f"schedule: success=false на {target_date}")
    return payload


def build_lookup(base: dict) -> dict[str, dict[str, str]]:
    """Строит словари UUID -> название для всех сущностей.

    Возвращает dict с ключами:
        divisions, groups, teachers, disciplines,
        lesson_types, auditoria, territories
    """

    def index(items, name_field="name"):
        return {it["id"]: it.get(name_field) or "—" for it in items}

    return {
        "divisions": index(base.get("divisions", [])),
        "groups": index(base.get("groups", [])),
        "teachers": index(base.get("teachers", [])),
        "disciplines": index(base.get("disciplines", [])),
        "lesson_types": index(base.get("lesson_Types", [])),
        "auditoria": {a["id"]: a.get("short_name") or a.get("name") or "—" for a in base.get("audithories", [])},
        "territories": index(base.get("territories", [])),
    }


# Визуально неотличимые символы: латиница -> кириллица
_LOOKALIKE = str.maketrans(
    {
        "a": "а",
        "c": "с",
        "e": "е",
        "o": "о",
        "p": "р",
        "x": "х",
        "y": "у",
        "k": "к",
        "m": "м",
        "t": "т",
        "h": "н",
        "b": "в",  # b/v визуально разные, но иногда путают
    }
)


def _normalize_division(s: str) -> str:
    """Приводит название подразделения к каноническому виду.

    'СП-4', 'СП4', '(СП-4)', 'сп 4', 'CП 4' (с латинской C) -> 'сп4'
    """
    s = s.lower()
    s = s.translate(_LOOKALIKE)  # латиница -> кириллица
    return re.sub(r"[^а-яa-z0-9]", "", s)


def find_group(base: dict, name: str, division_part: Optional[str] = None) -> Optional[dict]:
    division_id: Optional[str] = None
    if division_part:
        needle = _normalize_division(division_part)
        for d in base.get("divisions", []):
            if needle in _normalize_division(d["name"]):
                division_id = d["id"]
                break
        if division_id is None:
            return None

    target_name = name.strip().lower()
    for g in base.get("groups", []):
        if g["name"].strip().lower() != target_name:
            continue
        if division_id is None or g["division"] == division_id:
            return g
    return None


def get_group_lessons(base: dict, group_uuid: str, target_date: date_type) -> list[dict]:
    """Возвращает читаемые пары группы на дату.

    Каждая пара — словарь:
        {
            "number":       int,     # номер пары
            "subgroup":     int,     # 0 если вся группа
            "discipline":   str,     # название дисциплины
            "teacher":      str,     # ФИО преподавателя
            "auditoria":    str,     # кабинет (или "Дистант", "СР")
            "lesson_type":  str,     # тип занятия
            "territory":    str,     # номер подразделения ("СП-1", ..., "СП-5")
        }
    Список отсортирован по (number, subgroup).
    """
    lookup = build_lookup(base)
    payload = fetch_schedule(target_date)

    days = payload.get("schedule", {}).get("data", {}).get("schedule", [])

    # API иногда возвращает весь период. Ищем нужный день по timestamp.
    day = None
    for d in days:
        d_date = datetime.fromtimestamp(d["date"] / 1000).date()
        if d_date == target_date:
            day = d
            break

    if day is None:
        return []

    lessons = [l for l in day.get("lessons", []) if l["group"] == group_uuid]
    lessons.sort(key=lambda l: (l["number_lesson"], l["subgroup"]))

    result = []
    for l in lessons:
        result.append(
            {
                "number": l["number_lesson"],
                "subgroup": l["subgroup"],
                "discipline": lookup["disciplines"].get(l["discipline"], "—"),
                "teacher": lookup["teachers"].get(l["teacher"], "—"),
                "auditoria": lookup["auditoria"].get(l["auditoria"], "—"),
                "lesson_type": lookup["lesson_types"].get(l["lesson_type"], "—"),
                "territory": lookup["territories"].get(l.get("territory"), ""),
            }
        )
    return result


def get_week_lessons(base: dict, group_uuid: str, start_date: date_type) -> dict[date_type, list[dict]]:
    """Возвращает {дата: [пары]} для 7 дней начиная со start_date."""
    result: dict[date_type, list[dict]] = {}
    for offset in range(7):
        d = start_date + timedelta(days=offset)
        try:
            result[d] = get_group_lessons(base, group_uuid, d)
        except ScheduleAPIError as e:
            logger.warning("Не удалось получить %s: %s", d, e)
            result[d] = []
    return result


def home_territory(base: dict, group_uuid: str) -> str:
    """Возвращает название подразделения группы.

    Используется для сравнения с territory пары: если у пары
    territory отличается — значит, пара в другом корпусе.
    """
    for g in base.get("groups", []):
        if g["id"] == group_uuid:
            div_id = g["division"]
            for d in base.get("divisions", []):
                if d["id"] == div_id:
                    return d["name"]
    return ""
