"""Хранилище состояния бота на SQLite.

Модуль ничего не знает про HTTP и ВК — только про данные.
Все публичные функции потокобезопасны.

Публичный API:
    init_db(path)                    -> Connection
    # Настройки
    set_setting(conn, key, value)
    get_setting(conn, key, default=None)
    get_all_settings(conn)           -> dict
    delete_setting(conn, key)
    # Снимки
    save_snapshot(conn, group_uuid, date, lessons)   -> str (hash)
    get_snapshot(conn, group_uuid, date)             -> dict | None
    delete_snapshot(conn, group_uuid, date)
    cleanup_old_snapshots(conn, keep_days=30)
    # Кэш справочников
    save_base_info(conn, data)
    get_base_info(conn)              -> dict | None
    base_info_age_hours(conn)        -> float | None
    # Лог уведомлений
    log_notification(conn, group_uuid, date, kind, payload)
    last_notification(conn, group_uuid, date)        -> dict | None
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import date as date_type, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Глобальный лок для сериализации записей.
# Нужен, потому что ВК-бот (asyncio) и планировщик (поток) могут
# одновременно писать в БД.
_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    group_uuid TEXT NOT NULL,
    date       TEXT NOT NULL,
    hash       TEXT NOT NULL,
    lessons    TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_uuid, date)
);

CREATE TABLE IF NOT EXISTS base_info_cache (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    group_uuid TEXT NOT NULL,
    date       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    sent_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_lookup
    ON notifications_log (group_uuid, date, sent_at DESC);
"""


# ---------- Инициализация ----------

def init_db(path: str | Path = "bot.db") -> sqlite3.Connection:
    """Открывает/создаёт БД и применяет схему.

    Включает WAL-режим для параллельного чтения.
    Безопасен при многократном вызове.
    """
    path = str(path)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    with _write_lock:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        conn.commit()

    logger.info("БД инициализирована: %s", path)
    return conn


# ---------- Утилиты ----------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _date_key(d: date_type | str) -> str:
    """Унифицирует дату в формат 'YYYY-MM-DD'."""
    if isinstance(d, str):
        return d
    return d.isoformat()


# ---------- Настройки ----------

def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    """Сохраняет настройку. Значение всегда сериализуется в JSON.

    Это гарантирует, что при чтении вернётся ровно тот же тип:
        set_setting(conn, "k", "419")   # строка
        get_setting(conn, "k")           # '419' (str)

        set_setting(conn, "k", 419)      # число
        get_setting(conn, "k")           # 419 (int)
    """
    payload = json.dumps(value, ensure_ascii=False)
    with _write_lock:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, payload),
        )
        conn.commit()


def get_setting(
    conn: sqlite3.Connection, key: str, default: Any = None,
) -> Any:
    """Возвращает настройку или default.

    Значение всегда хранится как JSON — парсим его.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,),
    ).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        # На случай, если в БД лежит что-то не-JSON (старые данные,
        # ручная правка). Не падаем — возвращаем как есть.
        logger.warning("Настройка %s не JSON: %r", key, row["value"])
        return row["value"]


def get_all_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """Возвращает все настройки как dict."""
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    result = {}
    for r in rows:
        result[r["key"]] = get_setting(conn, r["key"])
    return result


def delete_setting(conn: sqlite3.Connection, key: str) -> None:
    with _write_lock:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()


# ---------- Снимки расписания ----------

def _lessons_hash(lessons: list[dict]) -> str:
    """Стабильный хеш снимка — сортируем ключи, JSON без пробелов."""
    import hashlib
    normalized = json.dumps(lessons, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def save_snapshot(
    conn: sqlite3.Connection,
    group_uuid: str,
    day: date_type | str,
    lessons: list[dict],
) -> str:
    """Сохраняет снимок. Возвращает хеш."""
    key = _date_key(day)
    payload = json.dumps(lessons, ensure_ascii=False, sort_keys=True)
    h = _lessons_hash(lessons)

    with _write_lock:
        conn.execute(
            "INSERT INTO snapshots (group_uuid, date, hash, lessons, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(group_uuid, date) DO UPDATE SET "
            "  hash = excluded.hash, "
            "  lessons = excluded.lessons, "
            "  updated_at = excluded.updated_at",
            (group_uuid, key, h, payload, _now()),
        )
        conn.commit()
    return h


def get_snapshot(
    conn: sqlite3.Connection, group_uuid: str, day: date_type | str,
) -> Optional[dict]:
    """Возвращает снимок или None.

    Формат: {"hash": str, "lessons": list[dict], "updated_at": str}
    """
    key = _date_key(day)
    row = conn.execute(
        "SELECT hash, lessons, updated_at FROM snapshots "
        "WHERE group_uuid = ? AND date = ?",
        (group_uuid, key),
    ).fetchone()
    if row is None:
        return None
    try:
        lessons = json.loads(row["lessons"])
    except ValueError:
        logger.error("Не удалось распарсить снимок %s / %s", group_uuid, key)
        return None
    return {
        "hash": row["hash"],
        "lessons": lessons,
        "updated_at": row["updated_at"],
    }


def delete_snapshot(
    conn: sqlite3.Connection, group_uuid: str, day: date_type | str,
) -> None:
    key = _date_key(day)
    with _write_lock:
        conn.execute(
            "DELETE FROM snapshots WHERE group_uuid = ? AND date = ?",
            (group_uuid, key),
        )
        conn.commit()


def cleanup_old_snapshots(
    conn: sqlite3.Connection, keep_days: int = 30,
) -> int:
    """Удаляет снимки старше keep_days. Возвращает число удалённых."""
    threshold = (datetime.now() - timedelta(days=keep_days)).date().isoformat()
    with _write_lock:
        cur = conn.execute(
            "DELETE FROM snapshots WHERE date < ?", (threshold,),
        )
        conn.commit()
    return cur.rowcount


# ---------- Кэш справочников ----------

def save_base_info(conn: sqlite3.Connection, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    with _write_lock:
        conn.execute(
            "INSERT INTO base_info_cache (id, data, updated_at) "
            "VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  data = excluded.data, "
            "  updated_at = excluded.updated_at",
            (payload, _now()),
        )
        conn.commit()


def get_base_info(conn: sqlite3.Connection) -> Optional[dict]:
    row = conn.execute(
        "SELECT data FROM base_info_cache WHERE id = 1",
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["data"])
    except ValueError:
        return None


def base_info_age_hours(conn: sqlite3.Connection) -> Optional[float]:
    """Сколько часов назад обновляли справочники. None если их нет."""
    row = conn.execute(
        "SELECT updated_at FROM base_info_cache WHERE id = 1",
    ).fetchone()
    if row is None:
        return None
    try:
        updated = datetime.fromisoformat(row["updated_at"])
    except ValueError:
        return None
    return (datetime.now() - updated).total_seconds() / 3600


# ---------- Лог уведомлений ----------

def log_notification(
    conn: sqlite3.Connection,
    group_uuid: str,
    day: date_type | str,
    kind: str,
    payload: Any,
) -> None:
    key = _date_key(day)
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    with _write_lock:
        conn.execute(
            "INSERT INTO notifications_log "
            "(group_uuid, date, kind, payload, sent_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (group_uuid, key, kind, payload, _now()),
        )
        conn.commit()


def last_notification(
    conn: sqlite3.Connection, group_uuid: str, day: date_type | str,
) -> Optional[dict]:
    """Последнее уведомление по (группа, дата) или None."""
    key = _date_key(day)
    row = conn.execute(
        "SELECT kind, payload, sent_at FROM notifications_log "
        "WHERE group_uuid = ? AND date = ? "
        "ORDER BY id DESC LIMIT 1",
        (group_uuid, key),
    ).fetchone()
    if row is None:
        return None
    return {
        "kind": row["kind"],
        "payload": row["payload"],
        "sent_at": row["sent_at"],
    }