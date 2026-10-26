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
    # Объявленные недели
    set_week_announced(conn, group_uuid, week_start, is_full)
    get_week_announced(conn, group_uuid, week_start) -> dict | None
    clear_week_announced(conn, group_uuid, week_start=None)
    cleanup_old_announced_weeks(conn, keep_days=30)
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

CREATE TABLE IF NOT EXISTS announced_weeks (
    group_uuid   TEXT NOT NULL,
    week_start   TEXT NOT NULL,
    announced_at TEXT NOT NULL,
    is_full      INTEGER NOT NULL,
    PRIMARY KEY (group_uuid, week_start)
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

CREATE TABLE IF NOT EXISTS week_images (
    group_uuid TEXT NOT NULL,
    week_start TEXT NOT NULL,
    image      BLOB NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_uuid, week_start)
);

CREATE TABLE IF NOT EXISTS contacts (
    peer_id      INTEGER NOT NULL,
    teacher_uuid TEXT NOT NULL,
    teacher_name TEXT NOT NULL,
    emails       TEXT NOT NULL DEFAULT '[]',
    telegram     TEXT NOT NULL DEFAULT '',
    tg_groups    TEXT NOT NULL DEFAULT '[]',
    vk           TEXT NOT NULL DEFAULT '',
    vk_groups    TEXT NOT NULL DEFAULT '[]',
    phones       TEXT NOT NULL DEFAULT '[]',
    folders      TEXT NOT NULL DEFAULT '[]',
    max_link     TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (peer_id, teacher_uuid)
);

CREATE TABLE IF NOT EXISTS student_contacts (
    peer_id      INTEGER NOT NULL,
    student_name TEXT NOT NULL,
    emails       TEXT NOT NULL DEFAULT '[]',
    telegram     TEXT NOT NULL DEFAULT '',
    tg_groups    TEXT NOT NULL DEFAULT '[]',
    vk           TEXT NOT NULL DEFAULT '',
    vk_groups    TEXT NOT NULL DEFAULT '[]',
    phones       TEXT NOT NULL DEFAULT '[]',
    folders      TEXT NOT NULL DEFAULT '[]',
    max_link     TEXT NOT NULL DEFAULT '',
    address      TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (peer_id, student_name)
);

-- ============================================================
-- Multi-tenant: группы, обслуживаемые ботом (одна беседа = одна группа)
-- ============================================================
CREATE TABLE IF NOT EXISTS groups (
    peer_id     INTEGER PRIMARY KEY,
    group_uuid  TEXT NOT NULL,
    group_name  TEXT NOT NULL,
    division    TEXT NOT NULL,
    curator_id  INTEGER,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

-- ============================================================
-- Преподаватели, обслуживаемые ботом (одна беседа = один преподаватель)
-- ============================================================
CREATE TABLE IF NOT EXISTS teachers (
    peer_id      INTEGER PRIMARY KEY,
    teacher_uuid TEXT NOT NULL,
    teacher_name TEXT NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);

-- ============================================================
-- Сессии в ЛС: у какого пользователя какая группа «активна»
-- ============================================================
CREATE TABLE IF NOT EXISTS user_sessions (
    user_id        INTEGER PRIMARY KEY,
    active_peer_id INTEGER,
    updated_at     TEXT NOT NULL
);

-- ============================================================
-- Все входящие сообщения во всех беседах (для поиска bell schedules)
-- ============================================================
CREATE TABLE IF NOT EXISTS chat_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id      INTEGER NOT NULL,
    message_id   INTEGER NOT NULL,
    from_id      INTEGER NOT NULL,
    text         TEXT NOT NULL DEFAULT '',
    attachments  TEXT NOT NULL DEFAULT '[]',
    sent_at      TEXT NOT NULL,
    ocr_done     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(peer_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_lookup
    ON chat_messages (peer_id, sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_chat_messages_from
    ON chat_messages (peer_id, from_id, sent_at DESC);

-- ============================================================
-- Найденные расписания звонков
-- ============================================================
CREATE TABLE IF NOT EXISTS bell_schedules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id       INTEGER NOT NULL,
    target_date   TEXT NOT NULL,
    source_msg_id INTEGER NOT NULL,
    from_id       INTEGER NOT NULL,
    image_bytes   BLOB,
    caption       TEXT NOT NULL DEFAULT '',
    ocr_text      TEXT NOT NULL DEFAULT '',
    detected_at   TEXT NOT NULL,
    sent_at       TEXT,
    UNIQUE(peer_id, target_date)
);

CREATE INDEX IF NOT EXISTS idx_bell_schedules_target
    ON bell_schedules (peer_id, target_date);

-- ============================================================
-- Флаги «пинг куратора отправлен» (чтобы не спамить)
-- ============================================================
CREATE TABLE IF NOT EXISTS holiday_pings (
    peer_id       INTEGER NOT NULL,
    holiday_date  TEXT NOT NULL,
    pinged_at     TEXT NOT NULL,
    PRIMARY KEY (peer_id, holiday_date)
);
"""


# ---------- Инициализация ----------


def init_db(path: str | Path = "bot.db") -> sqlite3.Connection:
    """Открывает/создаёт БД и применяет схему.

    Использует journal_mode=DELETE — все записи идут напрямую в bot.db,
    без промежуточного WAL-файла. Это надёжнее для деплоя на хостингах,
    где volume может быть пересоздан между перезапусками контейнера:
    при WAL-режиме данные живут в bot.db-wal и теряются, если файл
    не пережил пересборку.
    Безопасен при многократном вызове.
    """
    path = str(path)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    with _write_lock:
        conn.execute("PRAGMA journal_mode=DELETE")
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
    """Сохраняет настройку. Значение всегда сериализуется в JSON."""
    payload = json.dumps(value, ensure_ascii=False)
    with _write_lock:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) " "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, payload),
        )
        conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    """Возвращает настройку или default. Значение парсится из JSON."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
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


def save_snapshot(conn: sqlite3.Connection, group_uuid: str, day: date_type | str, lessons: list[dict]) -> str:
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


def get_snapshot(conn: sqlite3.Connection, group_uuid: str, day: date_type | str) -> Optional[dict]:
    """Возвращает снимок или None.

    Формат: {"hash": str, "lessons": list[dict], "updated_at": str}
    """
    key = _date_key(day)
    row = conn.execute(
        "SELECT hash, lessons, updated_at FROM snapshots " "WHERE group_uuid = ? AND date = ?", (group_uuid, key)
    ).fetchone()
    if row is None:
        return None
    try:
        lessons = json.loads(row["lessons"])
    except ValueError:
        logger.error("Не удалось распарсить снимок %s / %s", group_uuid, key)
        return None
    return {"hash": row["hash"], "lessons": lessons, "updated_at": row["updated_at"]}


def delete_snapshot(conn: sqlite3.Connection, group_uuid: str, day: date_type | str) -> None:
    key = _date_key(day)
    with _write_lock:
        conn.execute("DELETE FROM snapshots WHERE group_uuid = ? AND date = ?", (group_uuid, key))
        conn.commit()


def cleanup_old_snapshots(conn: sqlite3.Connection, keep_days: int = 30) -> int:
    """Удаляет снимки старше keep_days. Возвращает число удалённых."""
    threshold = (datetime.now() - timedelta(days=keep_days)).date().isoformat()
    with _write_lock:
        cur = conn.execute("DELETE FROM snapshots WHERE date < ?", (threshold,))
        conn.commit()
    return cur.rowcount


# ---------- Объявленные недели ----------


def set_week_announced(conn: sqlite3.Connection, group_uuid: str, week_start: date_type | str, is_full: bool) -> None:
    """Ставит флаг «неделя объявлена» для группы.

    Args:
        group_uuid: UUID группы.
        week_start: понедельник недели (date или 'YYYY-MM-DD').
        is_full:    True — отправлено полное расписание,
                    False — неполное (например, в воскресенье в 13:00).
    """
    key = _date_key(week_start)
    with _write_lock:
        conn.execute(
            "INSERT INTO announced_weeks "
            "(group_uuid, week_start, announced_at, is_full) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(group_uuid, week_start) DO UPDATE SET "
            "  announced_at = excluded.announced_at, "
            "  is_full = excluded.is_full",
            (group_uuid, key, _now(), 1 if is_full else 0),
        )
        conn.commit()


def get_week_announced(conn: sqlite3.Connection, group_uuid: str, week_start: date_type | str) -> Optional[dict]:
    """Возвращает запись о неделе или None.

    Формат: {"announced_at": str, "is_full": bool}
    """
    key = _date_key(week_start)
    row = conn.execute(
        "SELECT announced_at, is_full FROM announced_weeks " "WHERE group_uuid = ? AND week_start = ?",
        (group_uuid, key),
    ).fetchone()
    if row is None:
        return None
    return {"announced_at": row["announced_at"], "is_full": bool(row["is_full"])}


def clear_week_announced(
    conn: sqlite3.Connection, group_uuid: str, week_start: Optional[date_type | str] = None
) -> int:
    """Удаляет записи о неделях.

    Если week_start задан — только для этой недели.
    Если None — все записи для группы.

    Возвращает число удалённых строк.
    """
    with _write_lock:
        if week_start is None:
            cur = conn.execute("DELETE FROM announced_weeks WHERE group_uuid = ?", (group_uuid,))
        else:
            key = _date_key(week_start)
            cur = conn.execute(
                "DELETE FROM announced_weeks " "WHERE group_uuid = ? AND week_start = ?", (group_uuid, key)
            )
        conn.commit()
    return cur.rowcount


def cleanup_old_announced_weeks(conn: sqlite3.Connection, keep_days: int = 30) -> int:
    """Удаляет записи о неделях, чей week_start старше keep_days.

    Логика: если неделя закончилась больше keep_days назад, она не нужна.
    """
    threshold = (datetime.now() - timedelta(days=keep_days)).date().isoformat()
    with _write_lock:
        cur = conn.execute("DELETE FROM announced_weeks WHERE week_start < ?", (threshold,))
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
    row = conn.execute("SELECT data FROM base_info_cache WHERE id = 1").fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["data"])
    except ValueError:
        return None


def base_info_age_hours(conn: sqlite3.Connection) -> Optional[float]:
    """Сколько часов назад обновляли справочники. None если их нет."""
    row = conn.execute("SELECT updated_at FROM base_info_cache WHERE id = 1").fetchone()
    if row is None:
        return None
    try:
        updated = datetime.fromisoformat(row["updated_at"])
    except ValueError:
        return None
    return (datetime.now() - updated).total_seconds() / 3600


# ---------- Лог уведомлений ----------


def log_notification(conn: sqlite3.Connection, group_uuid: str, day: date_type | str, kind: str, payload: Any) -> None:
    key = _date_key(day)
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    with _write_lock:
        conn.execute(
            "INSERT INTO notifications_log " "(group_uuid, date, kind, payload, sent_at) " "VALUES (?, ?, ?, ?, ?)",
            (group_uuid, key, kind, payload, _now()),
        )
        conn.commit()


def last_notification(conn: sqlite3.Connection, group_uuid: str, day: date_type | str) -> Optional[dict]:
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
    return {"kind": row["kind"], "payload": row["payload"], "sent_at": row["sent_at"]}


# ---------- Изображения недели ----------


def save_week_image(conn: sqlite3.Connection, group_uuid: str, week_start: date_type | str, image: bytes) -> None:
    """Сохраняет PNG-картинку недели.

    Перезаписывает существующую (не копим старые снимки одной недели).
    """
    key = _date_key(week_start)
    with _write_lock:
        conn.execute(
            "INSERT INTO week_images (group_uuid, week_start, image, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(group_uuid, week_start) DO UPDATE SET "
            "  image = excluded.image, "
            "  updated_at = excluded.updated_at",
            (group_uuid, key, image, _now()),
        )
        conn.commit()


def get_week_image(conn: sqlite3.Connection, group_uuid: str, week_start: date_type | str) -> Optional[bytes]:
    """Возвращает PNG-картинку недели или None."""
    key = _date_key(week_start)
    row = conn.execute(
        "SELECT image FROM week_images WHERE group_uuid = ? AND week_start = ?", (group_uuid, key)
    ).fetchone()
    if row is None:
        return None
    return bytes(row["image"])


def delete_week_image(conn: sqlite3.Connection, group_uuid: str, week_start: date_type | str) -> None:
    key = _date_key(week_start)
    with _write_lock:
        conn.execute("DELETE FROM week_images WHERE group_uuid = ? AND week_start = ?", (group_uuid, key))
        conn.commit()


def cleanup_old_week_images(conn: sqlite3.Connection, keep_days: int = 30) -> int:
    """Удаляет картинки недель, чей week_start старше keep_days."""
    threshold = (datetime.now() - timedelta(days=keep_days)).date().isoformat()
    with _write_lock:
        cur = conn.execute("DELETE FROM week_images WHERE week_start < ?", (threshold,))
        conn.commit()
    return cur.rowcount


# ============================================================
# Multi-tenant: группы
# ============================================================


def add_group(
    conn: sqlite3.Connection,
    peer_id: int,
    group_uuid: str,
    group_name: str,
    division: str,
    curator_id: int | None = None,
) -> None:
    """Регистрирует беседу как «группу», за которой следит бот.

    Если запись с таким peer_id уже есть — обновляет поля
    (group_uuid, group_name, division) и оставляет curator_id,
    если он не передан явно.
    """
    with _write_lock:
        # Удаляем преподавателя, если был (беседа либо группа, либо преподаватель)
        conn.execute("DELETE FROM teachers WHERE peer_id = ?", (peer_id,))
        conn.execute(
            "INSERT INTO groups "
            "(peer_id, group_uuid, group_name, division, curator_id, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?) "
            "ON CONFLICT(peer_id) DO UPDATE SET "
            "  group_uuid = excluded.group_uuid, "
            "  group_name = excluded.group_name, "
            "  division   = excluded.division, "
            "  curator_id = COALESCE(excluded.curator_id, groups.curator_id)",
            (peer_id, group_uuid, group_name, division, curator_id, _now()),
        )
        conn.commit()


def get_group(conn: sqlite3.Connection, peer_id: int) -> Optional[dict]:
    """Возвращает запись о группе или None.

    Формат:
        {"peer_id": int, "group_uuid": str, "group_name": str,
         "division": str, "curator_id": int | None,
         "is_active": bool, "created_at": str}
    """
    row = conn.execute(
        "SELECT peer_id, group_uuid, group_name, division, curator_id, "
        "       is_active, created_at "
        "FROM groups WHERE peer_id = ?",
        (peer_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "peer_id": row["peer_id"],
        "group_uuid": row["group_uuid"],
        "group_name": row["group_name"],
        "division": row["division"],
        "curator_id": row["curator_id"],
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
    }


def get_all_groups(conn: sqlite3.Connection, only_active: bool = True) -> list[dict]:
    """Список всех групп.

    Args:
        only_active: если True — только записи с is_active=1.

    Returns:
        Список dict в формате get_group.
    """
    sql = "SELECT peer_id, group_uuid, group_name, division, curator_id, " "       is_active, created_at " "FROM groups"
    params: tuple = ()
    if only_active:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY created_at"
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "peer_id": r["peer_id"],
            "group_uuid": r["group_uuid"],
            "group_name": r["group_name"],
            "division": r["division"],
            "curator_id": r["curator_id"],
            "is_active": bool(r["is_active"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def remove_group(conn: sqlite3.Connection, peer_id: int) -> None:
    """Полностью удаляет группу из БД (все её настройки)."""
    with _write_lock:
        conn.execute("DELETE FROM groups WHERE peer_id = ?", (peer_id,))
        conn.commit()


def set_group_curator(conn: sqlite3.Connection, peer_id: int, curator_id: int | None) -> None:
    """Устанавливает ID куратора для группы. None — сбросить."""
    with _write_lock:
        conn.execute("UPDATE groups SET curator_id = ? WHERE peer_id = ?", (curator_id, peer_id))
        conn.commit()


def set_group_active(conn: sqlite3.Connection, peer_id: int, is_active: bool) -> None:
    """Включает/выключает группу. При is_active=0 цикл её не проверяет."""
    with _write_lock:
        conn.execute("UPDATE groups SET is_active = ? WHERE peer_id = ?", (1 if is_active else 0, peer_id))
        conn.commit()


# ============================================================
# Multi-tenant: преподаватели
# ============================================================


def add_teacher(conn: sqlite3.Connection, peer_id: int, teacher_uuid: str, teacher_name: str) -> None:
    """Регистрирует беседу как преподавателя.

    Взаимоисключение: если в этой беседе была группа — она удаляется.
    """
    with _write_lock:
        # Удаляем группу, если была (беседа либо группа, либо препод)
        conn.execute("DELETE FROM groups WHERE peer_id = ?", (peer_id,))
        conn.execute(
            "INSERT INTO teachers (peer_id, teacher_uuid, teacher_name, is_active, created_at) "
            "VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(peer_id) DO UPDATE SET "
            "  teacher_uuid = excluded.teacher_uuid, "
            "  teacher_name = excluded.teacher_name",
            (peer_id, teacher_uuid, teacher_name, _now()),
        )
        conn.commit()


def get_teacher(conn: sqlite3.Connection, peer_id: int) -> Optional[dict]:
    """Возвращает запись о преподавателе или None.

    Формат:
        {"peer_id": int, "teacher_uuid": str, "teacher_name": str,
         "is_active": bool, "created_at": str}
    """
    row = conn.execute(
        "SELECT peer_id, teacher_uuid, teacher_name, is_active, created_at " "FROM teachers WHERE peer_id = ?",
        (peer_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "peer_id": row["peer_id"],
        "teacher_uuid": row["teacher_uuid"],
        "teacher_name": row["teacher_name"],
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
    }


def get_all_teachers(conn: sqlite3.Connection, only_active: bool = True) -> list[dict]:
    """Список всех преподавателей."""
    sql = "SELECT peer_id, teacher_uuid, teacher_name, is_active, created_at FROM teachers"
    params: tuple = ()
    if only_active:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY created_at"
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "peer_id": r["peer_id"],
            "teacher_uuid": r["teacher_uuid"],
            "teacher_name": r["teacher_name"],
            "is_active": bool(r["is_active"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def remove_teacher(conn: sqlite3.Connection, peer_id: int) -> None:
    """Удаляет преподавателя из БД."""
    with _write_lock:
        conn.execute("DELETE FROM teachers WHERE peer_id = ?", (peer_id,))
        conn.commit()


def set_teacher_active(conn: sqlite3.Connection, peer_id: int, is_active: bool) -> None:
    """Включает/выключает преподавателя."""
    with _write_lock:
        conn.execute("UPDATE teachers SET is_active = ? WHERE peer_id = ?", (1 if is_active else 0, peer_id))
        conn.commit()


def get_entity(conn: sqlite3.Connection, peer_id: int) -> Optional[dict]:
    """Возвращает сущность, привязанную к беседе (группа или преподаватель).

    В словарь добавляется ключ "type": "group" | "teacher".
    Если ничего не найдено — None.
    """
    g = get_group(conn, peer_id)
    if g is not None:
        g["type"] = "group"
        return g
    t = get_teacher(conn, peer_id)
    if t is not None:
        t["type"] = "teacher"
        return t
    return None


# ============================================================
# Сессии пользователей (для /use в ЛС)
# ============================================================


def set_user_session(conn: sqlite3.Connection, user_id: int, active_peer_id: int | None) -> None:
    """Запоминает, какую группу пользователь «выбрал» командой /use в ЛС."""
    with _write_lock:
        conn.execute(
            "INSERT INTO user_sessions (user_id, active_peer_id, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "  active_peer_id = excluded.active_peer_id, "
            "  updated_at     = excluded.updated_at",
            (user_id, active_peer_id, _now()),
        )
        conn.commit()


def get_user_session(conn: sqlite3.Connection, user_id: int) -> Optional[int]:
    """Возвращает active_peer_id для пользователя или None."""
    row = conn.execute("SELECT active_peer_id FROM user_sessions WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return row["active_peer_id"]


def clear_user_session(conn: sqlite3.Connection, user_id: int) -> None:
    with _write_lock:
        conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
        conn.commit()


# ============================================================
# Входящие сообщения чата
# ============================================================


def save_chat_message(
    conn: sqlite3.Connection,
    peer_id: int,
    message_id: int,
    from_id: int,
    text: str,
    attachments: list | dict | None = None,
) -> int:
    """Сохраняет входящее сообщение.

    Если запись с (peer_id, message_id) уже есть — игнорируется
    (Long Poll может доставить повторно при реконнекте).

    Returns:
        id записи в chat_messages (существующей или новой).
    """
    key = json.dumps(attachments or [], ensure_ascii=False)
    with _write_lock:
        conn.execute(
            "INSERT INTO chat_messages "
            "(peer_id, message_id, from_id, text, attachments, sent_at, ocr_done) "
            "VALUES (?, ?, ?, ?, ?, ?, 0) "
            "ON CONFLICT(peer_id, message_id) DO NOTHING",
            (peer_id, message_id, from_id, text or "", key, _now()),
        )
        conn.commit()
    row = conn.execute(
        "SELECT id FROM chat_messages WHERE peer_id = ? AND message_id = ?", (peer_id, message_id)
    ).fetchone()
    return row["id"] if row else 0


def get_chat_message(conn: sqlite3.Connection, peer_id: int, message_id: int) -> Optional[dict]:
    """Возвращает одно сообщение или None."""
    row = conn.execute(
        "SELECT id, peer_id, message_id, from_id, text, attachments, "
        "       sent_at, ocr_done "
        "FROM chat_messages WHERE peer_id = ? AND message_id = ?",
        (peer_id, message_id),
    ).fetchone()
    if row is None:
        return None
    try:
        attachments = json.loads(row["attachments"])
    except (ValueError, TypeError):
        attachments = []
    return {
        "id": row["id"],
        "peer_id": row["peer_id"],
        "message_id": row["message_id"],
        "from_id": row["from_id"],
        "text": row["text"],
        "attachments": attachments,
        "sent_at": row["sent_at"],
        "ocr_done": bool(row["ocr_done"]),
    }


def get_chat_messages_since(conn: sqlite3.Connection, peer_id: int, since_iso: str, *, limit: int = 1000) -> list[dict]:
    """Сообщения в чате начиная с since_iso (включительно), по возрастанию времени.

    Args:
        peer_id: ID беседы.
        since_iso: ISO timestamp, «YYYY-MM-DDTHH:MM:SS» или просто «YYYY-MM-DD».
        limit: максимум записей.
    """
    rows = conn.execute(
        "SELECT id, peer_id, message_id, from_id, text, attachments, "
        "       sent_at, ocr_done "
        "FROM chat_messages "
        "WHERE peer_id = ? AND sent_at >= ? "
        "ORDER BY sent_at ASC LIMIT ?",
        (peer_id, since_iso, limit),
    ).fetchall()
    result = []
    for r in rows:
        try:
            attachments = json.loads(r["attachments"])
        except (ValueError, TypeError):
            attachments = []
        result.append(
            {
                "id": r["id"],
                "peer_id": r["peer_id"],
                "message_id": r["message_id"],
                "from_id": r["from_id"],
                "text": r["text"],
                "attachments": attachments,
                "sent_at": r["sent_at"],
                "ocr_done": bool(r["ocr_done"]),
            }
        )
    return result


def count_chat_messages_since(
    conn: sqlite3.Connection, peer_id: int, since_iso: str, until_iso: str, *, exclude_from_id: int | None = None
) -> int:
    """Считает сообщения в чате в интервале [since_iso, until_iso].

    Args:
        exclude_from_id: если задан — не считаем сообщения от этого from_id
                         (например, самого бота, если он попадёт в лог).
    """
    sql = "SELECT COUNT(*) AS n FROM chat_messages " "WHERE peer_id = ? AND sent_at >= ? AND sent_at <= ?"
    params: list = [peer_id, since_iso, until_iso]
    if exclude_from_id is not None:
        sql += " AND from_id != ?"
        params.append(exclude_from_id)
    row = conn.execute(sql, tuple(params)).fetchone()
    return int(row["n"]) if row else 0


def mark_chat_message_ocr_done(conn: sqlite3.Connection, peer_id: int, message_id: int) -> None:
    """Ставит флаг, что OCR для этого сообщения уже прогоняли."""
    with _write_lock:
        conn.execute(
            "UPDATE chat_messages SET ocr_done = 1 " "WHERE peer_id = ? AND message_id = ?", (peer_id, message_id)
        )
        conn.commit()


def cleanup_old_chat_messages(conn: sqlite3.Connection, keep_days: int = 30) -> int:
    """Удаляет сообщения старше keep_days. Возвращает число удалённых."""
    threshold = (datetime.now() - timedelta(days=keep_days)).isoformat(timespec="seconds")
    with _write_lock:
        cur = conn.execute("DELETE FROM chat_messages WHERE sent_at < ?", (threshold,))
        conn.commit()
    return cur.rowcount


# ============================================================
# Расписания звонков
# ============================================================


def save_bell_schedule(
    conn: sqlite3.Connection,
    peer_id: int,
    target_date: date_type | str,
    source_msg_id: int,
    from_id: int,
    *,
    image_bytes: bytes | None = None,
    caption: str = "",
    ocr_text: str = "",
) -> None:
    """Сохраняет (или обновляет) расписание звонков на дату.

    UNIQUE(peer_id, target_date) — если для этой даты уже есть запись,
    перезаписываем (свежая версия вытесняет старую). Поле sent_at
    сбрасывается — значит, новую версию надо будет отправить в чат.
    """
    key = _date_key(target_date)
    with _write_lock:
        conn.execute(
            "INSERT INTO bell_schedules "
            "(peer_id, target_date, source_msg_id, from_id, image_bytes, "
            " caption, ocr_text, detected_at, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL) "
            "ON CONFLICT(peer_id, target_date) DO UPDATE SET "
            "  source_msg_id = excluded.source_msg_id, "
            "  from_id       = excluded.from_id, "
            "  image_bytes   = excluded.image_bytes, "
            "  caption       = excluded.caption, "
            "  ocr_text      = excluded.ocr_text, "
            "  detected_at   = excluded.detected_at, "
            "  sent_at       = NULL",
            (peer_id, key, source_msg_id, from_id, image_bytes, caption, ocr_text, _now()),
        )
        conn.commit()


def get_bell_schedule(conn: sqlite3.Connection, peer_id: int, target_date: date_type | str) -> Optional[dict]:
    """Возвращает расписание звонков на дату или None."""
    key = _date_key(target_date)
    row = conn.execute(
        "SELECT id, peer_id, target_date, source_msg_id, from_id, "
        "       image_bytes, caption, ocr_text, detected_at, sent_at "
        "FROM bell_schedules WHERE peer_id = ? AND target_date = ?",
        (peer_id, key),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "peer_id": row["peer_id"],
        "target_date": row["target_date"],
        "source_msg_id": row["source_msg_id"],
        "from_id": row["from_id"],
        "image_bytes": bytes(row["image_bytes"]) if row["image_bytes"] else None,
        "caption": row["caption"],
        "ocr_text": row["ocr_text"],
        "detected_at": row["detected_at"],
        "sent_at": row["sent_at"],
    }


def mark_bell_schedule_sent(conn: sqlite3.Connection, peer_id: int, target_date: date_type | str) -> None:
    """Помечает, что расписание звонков отправлено в чат."""
    key = _date_key(target_date)
    with _write_lock:
        conn.execute(
            "UPDATE bell_schedules SET sent_at = ? " "WHERE peer_id = ? AND target_date = ?", (_now(), peer_id, key)
        )
        conn.commit()


def delete_bell_schedule(conn: sqlite3.Connection, peer_id: int, target_date: date_type | str) -> None:
    key = _date_key(target_date)
    with _write_lock:
        conn.execute("DELETE FROM bell_schedules WHERE peer_id = ? AND target_date = ?", (peer_id, key))
        conn.commit()


def cleanup_old_bell_schedules(conn: sqlite3.Connection) -> int:
    """Удаляет расписания звонков, чья target_date уже прошла.

    Правило: 14.10.26 в 00:00 удаляется запись с target_date=13.10.26.
    """
    today = datetime.now().date().isoformat()
    with _write_lock:
        cur = conn.execute("DELETE FROM bell_schedules WHERE target_date < ?", (today,))
        conn.commit()
    return cur.rowcount


# ============================================================
# Флаги «пинг куратора отправлен»
# ============================================================


def mark_holiday_pinged(conn: sqlite3.Connection, peer_id: int, holiday_date: date_type | str) -> None:
    """Помечает, что пинг куратора по празднику отправлен."""
    key = _date_key(holiday_date)
    with _write_lock:
        conn.execute(
            "INSERT INTO holiday_pings (peer_id, holiday_date, pinged_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(peer_id, holiday_date) DO UPDATE SET "
            "  pinged_at = excluded.pinged_at",
            (peer_id, key, _now()),
        )
        conn.commit()


def was_holiday_pinged(conn: sqlite3.Connection, peer_id: int, holiday_date: date_type | str) -> bool:
    """True, если пинг куратора по этой дате уже был."""
    key = _date_key(holiday_date)
    row = conn.execute("SELECT 1 FROM holiday_pings WHERE peer_id = ? AND holiday_date = ?", (peer_id, key)).fetchone()
    return row is not None


def cleanup_old_holiday_pings(conn: sqlite3.Connection, keep_days: int = 30) -> int:
    """Удаляет флаги пингов старше keep_days."""
    threshold = (datetime.now() - timedelta(days=keep_days)).date().isoformat()
    with _write_lock:
        cur = conn.execute("DELETE FROM holiday_pings WHERE holiday_date < ?", (threshold,))
        conn.commit()
    return cur.rowcount


# ============================================================
# Ежедневная очистка (запускается из background_checker)
# ============================================================

CLEANUP_INTERVAL_HOURS = 23  # с запасом: "раз в сутки, но не строго в 00:00"


def should_run_daily_cleanup(conn: sqlite3.Connection) -> bool:
    """True, если с прошлой очистки прошло >CLEANUP_INTERVAL_HOURS часов.

    Метка хранится в settings['last_cleanup_at']. Если метки нет —
    считается, что очистка не проводилась.
    """
    raw = get_setting(conn, "last_cleanup_at")
    if not raw:
        return True
    try:
        last = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return True
    delta = datetime.now() - last
    return delta.total_seconds() >= CLEANUP_INTERVAL_HOURS * 3600


def mark_daily_cleanup_done(conn: sqlite3.Connection) -> None:
    """Ставит метку «очистка выполнена только что»."""
    set_setting(conn, "last_cleanup_at", _now())


# ============================================================
# Миграция settings → groups (однократная, при старте)
# ============================================================


def migrate_settings_to_groups(conn: sqlite3.Connection) -> bool:
    """Переносит старые одиночные настройки в таблицу groups.

    Если в settings есть peer_id и group_uuid, а в groups пусто —
    создаёт одну запись. Старые ключи (peer_id, group_uuid, group_name,
    division) удаляются из settings.

    Returns:
        True, если миграция выполнена; False, если мигрировать нечего.
    """
    # Уже есть группы — не трогаем
    existing = conn.execute("SELECT COUNT(*) AS n FROM groups").fetchone()
    if existing and existing["n"] > 0:
        return False

    peer_id_raw = get_setting(conn, "peer_id")
    group_uuid = get_setting(conn, "group_uuid")
    group_name = get_setting(conn, "group_name")
    division = get_setting(conn, "division")

    if not peer_id_raw or not group_uuid:
        return False

    try:
        peer_id = int(peer_id_raw)
    except (ValueError, TypeError):
        logger.warning("migrate_settings_to_groups: peer_id не число: %r", peer_id_raw)
        return False

    add_group(
        conn,
        peer_id=peer_id,
        group_uuid=str(group_uuid),
        group_name=str(group_name or ""),
        division=str(division or ""),
        curator_id=None,
    )

    logger.info("Миграция settings→groups: peer_id=%s, group=%s", peer_id, group_name)
    return True


# ============================================================
# Контакты преподавателей
# ============================================================


def _json_list(val: str) -> list[str]:
    if not val:
        return []
    try:
        return json.loads(val)
    except (ValueError, TypeError):
        return []


def save_contact(
    conn: sqlite3.Connection,
    peer_id: int,
    teacher_uuid: str,
    teacher_name: str,
    *,
    emails: list[str] | None = None,
    telegram: str = "",
    tg_groups: list[str] | None = None,
    vk: str = "",
    vk_groups: list[str] | None = None,
    phones: list[str] | None = None,
    folders: list[str] | None = None,
    max_link: str = "",
) -> None:
    """Полностью сохраняет (или перезаписывает) контакты препода."""
    now = _now()
    with _write_lock:
        conn.execute(
            "INSERT INTO contacts "
            "(peer_id, teacher_uuid, teacher_name, emails, telegram, tg_groups, "
            " vk, vk_groups, phones, folders, max_link, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(peer_id, teacher_uuid) DO UPDATE SET "
            "  teacher_name = excluded.teacher_name, "
            "  emails       = excluded.emails, "
            "  telegram     = excluded.telegram, "
            "  tg_groups    = excluded.tg_groups, "
            "  vk           = excluded.vk, "
            "  vk_groups    = excluded.vk_groups, "
            "  phones       = excluded.phones, "
            "  folders      = excluded.folders, "
            "  max_link     = excluded.max_link, "
            "  updated_at   = excluded.updated_at",
            (
                peer_id,
                teacher_uuid,
                teacher_name,
                json.dumps(emails or [], ensure_ascii=False),
                telegram,
                json.dumps(tg_groups or [], ensure_ascii=False),
                vk,
                json.dumps(vk_groups or [], ensure_ascii=False),
                json.dumps(phones or [], ensure_ascii=False),
                json.dumps(folders or [], ensure_ascii=False),
                max_link,
                now,
            ),
        )
        conn.commit()


def update_contact_field(
    conn: sqlite3.Connection, peer_id: int, teacher_uuid: str, field: str, value: str | list[str]
) -> bool:
    """Обновляет конкретное поле контакта. Возвращает True, если запись была."""
    valid_fields = {"emails", "telegram", "tg_groups", "vk", "vk_groups", "phones", "folders", "max_link"}
    if field not in valid_fields:
        raise ValueError(f"Неизвестное поле контакта: {field}")

    # Для списков сериализуем в JSON, для строк оставляем как есть
    if field in {"emails", "tg_groups", "vk_groups", "phones", "folders"}:
        db_value = json.dumps(value if isinstance(value, list) else [value], ensure_ascii=False)
    else:
        db_value = str(value)

    now = _now()
    with _write_lock:
        cur = conn.execute(
            f"UPDATE contacts SET {field} = ?, updated_at = ? " "WHERE peer_id = ? AND teacher_uuid = ?",
            (db_value, now, peer_id, teacher_uuid),
        )
        conn.commit()
    return cur.rowcount > 0


def get_contact(conn: sqlite3.Connection, peer_id: int, teacher_uuid: str) -> Optional[dict]:
    """Возвращает контакт преподавателя для конкретной беседы."""
    row = conn.execute(
        "SELECT * FROM contacts WHERE peer_id = ? AND teacher_uuid = ?", (peer_id, teacher_uuid)
    ).fetchone()
    if row is None:
        return None

    return {
        "peer_id": row["peer_id"],
        "teacher_uuid": row["teacher_uuid"],
        "teacher_name": row["teacher_name"],
        "emails": _json_list(row["emails"]),
        "telegram": row["telegram"],
        "tg_groups": _json_list(row["tg_groups"]),
        "vk": row["vk"],
        "vk_groups": _json_list(row["vk_groups"]),
        "phones": _json_list(row["phones"]),
        "folders": _json_list(row["folders"]),
        "max_link": row["max_link"],
        "updated_at": row["updated_at"],
    }


def get_contacts_by_name(conn: sqlite3.Connection, peer_id: int, name_query: str) -> list[dict]:
    """Ищет контакты по подстроке в ФИО (только для указанной беседы)."""
    rows = conn.execute(
        "SELECT * FROM contacts WHERE peer_id = ? AND teacher_name LIKE ? ORDER BY teacher_name",
        (peer_id, f"%{name_query}%"),
    ).fetchall()

    result = []
    for row in rows:
        result.append(
            {
                "peer_id": row["peer_id"],
                "teacher_uuid": row["teacher_uuid"],
                "teacher_name": row["teacher_name"],
                "emails": _json_list(row["emails"]),
                "telegram": row["telegram"],
                "tg_groups": _json_list(row["tg_groups"]),
                "vk": row["vk"],
                "vk_groups": _json_list(row["vk_groups"]),
                "phones": _json_list(row["phones"]),
                "folders": _json_list(row["folders"]),
                "max_link": row["max_link"],
                "updated_at": row["updated_at"],
            }
        )
    return result


def get_all_contacts(conn: sqlite3.Connection, peer_id: int) -> list[dict]:
    """Возвращает все контакты для беседы."""
    rows = conn.execute("SELECT * FROM contacts WHERE peer_id = ? ORDER BY teacher_name", (peer_id,)).fetchall()

    result = []
    for row in rows:
        result.append(
            {
                "peer_id": row["peer_id"],
                "teacher_uuid": row["teacher_uuid"],
                "teacher_name": row["teacher_name"],
                "emails": _json_list(row["emails"]),
                "telegram": row["telegram"],
                "tg_groups": _json_list(row["tg_groups"]),
                "vk": row["vk"],
                "vk_groups": _json_list(row["vk_groups"]),
                "phones": _json_list(row["phones"]),
                "folders": _json_list(row["folders"]),
                "max_link": row["max_link"],
                "updated_at": row["updated_at"],
            }
        )
    return result


def remove_contact(conn: sqlite3.Connection, peer_id: int, teacher_uuid: str) -> bool:
    """Удаляет контакт преподавателя. Возвращает True, если запись была."""
    with _write_lock:
        cur = conn.execute("DELETE FROM contacts WHERE peer_id = ? AND teacher_uuid = ?", (peer_id, teacher_uuid))
        conn.commit()
    return cur.rowcount > 0


# ============================================================
# Контакты студентов
# ============================================================


def save_student_contact(
    conn: sqlite3.Connection,
    peer_id: int,
    student_name: str,
    *,
    emails: list[str] | None = None,
    telegram: str = "",
    tg_groups: list[str] | None = None,
    vk: str = "",
    vk_groups: list[str] | None = None,
    phones: list[str] | None = None,
    folders: list[str] | None = None,
    max_link: str = "",
    address: str = "",
) -> None:
    now = _now()
    with _write_lock:
        conn.execute(
            "INSERT INTO student_contacts "
            "(peer_id, student_name, emails, telegram, tg_groups, vk, vk_groups, phones, folders, max_link, address, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(peer_id, student_name) DO UPDATE SET "
            "  emails = excluded.emails, telegram = excluded.telegram, tg_groups = excluded.tg_groups, "
            "  vk = excluded.vk, vk_groups = excluded.vk_groups, phones = excluded.phones, "
            "  folders = excluded.folders, max_link = excluded.max_link, address = excluded.address, "
            "  updated_at = excluded.updated_at",
            (
                peer_id,
                student_name,
                json.dumps(emails or [], ensure_ascii=False),
                telegram,
                json.dumps(tg_groups or [], ensure_ascii=False),
                vk,
                json.dumps(vk_groups or [], ensure_ascii=False),
                json.dumps(phones or [], ensure_ascii=False),
                json.dumps(folders or [], ensure_ascii=False),
                max_link,
                address,
                now,
            ),
        )
        conn.commit()


def get_student_contact(conn: sqlite3.Connection, peer_id: int, student_name: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM student_contacts WHERE peer_id = ? AND student_name = ?", (peer_id, student_name)
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def get_students_by_name(conn: sqlite3.Connection, peer_id: int, name_query: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM student_contacts WHERE peer_id = ? AND student_name LIKE ? ORDER BY student_name",
        (peer_id, f"%{name_query}%"),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_all_students(conn: sqlite3.Connection, peer_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM student_contacts WHERE peer_id = ? ORDER BY student_name", (peer_id,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def remove_student_contact(conn: sqlite3.Connection, peer_id: int, student_name: str) -> bool:
    with _write_lock:
        cur = conn.execute(
            "DELETE FROM student_contacts WHERE peer_id = ? AND student_name = ?", (peer_id, student_name)
        )
        conn.commit()
    return cur.rowcount > 0


def update_student_contact_field(
    conn: sqlite3.Connection, peer_id: int, student_name: str, field: str, value: str | list[str]
) -> bool:
    """Обновляет конкретное поле контакта студента. Возвращает True, если запись была."""
    valid_fields = {"emails", "telegram", "tg_groups", "vk", "vk_groups", "phones", "folders", "max_link", "address"}
    if field not in valid_fields:
        raise ValueError(f"Неизвестное поле контакта: {field}")

    if field in {"emails", "tg_groups", "vk_groups", "phones", "folders"}:
        db_value = json.dumps(value if isinstance(value, list) else [value], ensure_ascii=False)
    else:
        db_value = str(value)

    now = _now()
    with _write_lock:
        cur = conn.execute(
            f"UPDATE student_contacts SET {field} = ?, updated_at = ? " "WHERE peer_id = ? AND student_name = ?",
            (db_value, now, peer_id, student_name),
        )
        conn.commit()
    return cur.rowcount > 0


def _row_to_dict(row) -> dict:
    """Хелпер для преобразования строки БД в словарь."""
    return {
        "peer_id": row["peer_id"],
        "student_name": row["student_name"],
        "emails": _json_list(row["emails"]),
        "telegram": row["telegram"],
        "tg_groups": _json_list(row["tg_groups"]),
        "vk": row["vk"],
        "vk_groups": _json_list(row["vk_groups"]),
        "phones": _json_list(row["phones"]),
        "folders": _json_list(row["folders"]),
        "max_link": row["max_link"],
        "address": row["address"],
        "updated_at": row["updated_at"],
    }
