"""Тесты для database.py.

Используют временный файл БД (tmp_path от pytest) — реальная БД не трогается.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from database import (
    base_info_age_hours,
    cleanup_old_snapshots,
    delete_setting,
    delete_snapshot,
    get_all_settings,
    get_base_info,
    get_setting,
    get_snapshot,
    init_db,
    last_notification,
    log_notification,
    save_base_info,
    save_snapshot,
    set_setting,
)


@pytest.fixture
def conn(tmp_path):
    """Свежая БД на каждый тест."""
    db_path = tmp_path / "test.db"
    c = init_db(db_path)
    yield c
    c.close()


# ---------- init_db ----------

def test_init_db_creates_tables(conn):
    tables = {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"settings", "snapshots", "base_info_cache",
            "notifications_log"} <= tables


def test_init_db_idempotent(tmp_path):
    """Многократный init не падает и не ломает схему."""
    p = tmp_path / "x.db"
    c1 = init_db(p); c1.close()
    c2 = init_db(p); c2.close()
    c3 = init_db(p); c3.close()


def test_init_db_wal_mode(conn):
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


# ---------- Настройки ----------

def test_set_get_setting(conn):
    set_setting(conn, "group_name", "419")
    assert get_setting(conn, "group_name") == "419"


def test_get_setting_default(conn):
    assert get_setting(conn, "missing", "fallback") == "fallback"
    assert get_setting(conn, "missing") is None


def test_set_setting_overwrites(conn):
    set_setting(conn, "k", "v1")
    set_setting(conn, "k", "v2")
    assert get_setting(conn, "k") == "v2"


def test_set_setting_non_string(conn):
    set_setting(conn, "interval", 30)
    assert get_setting(conn, "interval") == 30
    set_setting(conn, "flags", {"a": True, "b": [1, 2]})
    assert get_setting(conn, "flags") == {"a": True, "b": [1, 2]}


def test_get_all_settings(conn):
    set_setting(conn, "a", 1)
    set_setting(conn, "b", "two")
    result = get_all_settings(conn)
    assert result == {"a": 1, "b": "two"}


def test_delete_setting(conn):
    set_setting(conn, "k", "v")
    delete_setting(conn, "k")
    assert get_setting(conn, "k") is None


# ---------- Снимки ----------

def test_save_and_get_snapshot(conn):
    lessons = [
        {"number": 1, "subgroup": 0, "discipline": "Web",
         "teacher": "Дубров", "auditoria": "43", "lesson_type": "Лекция"},
    ]
    h = save_snapshot(conn, "grp-1", date(2026, 9, 12), lessons)
    assert isinstance(h, str) and len(h) == 64  # sha256 hex

    snap = get_snapshot(conn, "grp-1", date(2026, 9, 12))
    assert snap is not None
    assert snap["hash"] == h
    assert snap["lessons"] == lessons


def test_snapshot_hash_is_stable(conn):
    """Один и тот же снимок даёт один и тот же хеш."""
    lessons = [{"number": 1, "subgroup": 0, "discipline": "X",
                "teacher": "Y", "auditoria": "1", "lesson_type": "L"}]
    h1 = save_snapshot(conn, "grp-1", date(2026, 9, 12), lessons)
    h2 = save_snapshot(conn, "grp-1", date(2026, 9, 12), lessons)
    assert h1 == h2


def test_snapshot_hash_changes_on_content(conn):
    l1 = [{"number": 1, "subgroup": 0, "discipline": "A",
           "teacher": "T", "auditoria": "1", "lesson_type": "L"}]
    l2 = [{"number": 1, "subgroup": 0, "discipline": "B",
           "teacher": "T", "auditoria": "1", "lesson_type": "L"}]
    h1 = save_snapshot(conn, "grp-1", date(2026, 9, 12), l1)
    h2 = save_snapshot(conn, "grp-1", date(2026, 9, 12), l2)
    assert h1 != h2


def test_get_snapshot_missing(conn):
    assert get_snapshot(conn, "grp-x", date(2026, 9, 12)) is None


def test_delete_snapshot(conn):
    save_snapshot(conn, "grp-1", date(2026, 9, 12), [])
    delete_snapshot(conn, "grp-1", date(2026, 9, 12))
    assert get_snapshot(conn, "grp-1", date(2026, 9, 12)) is None


def test_cleanup_old_snapshots(conn):
    today = date(2026, 9, 12)
    save_snapshot(conn, "grp-1", today, [])
    save_snapshot(conn, "grp-1", today - timedelta(days=100), [])
    save_snapshot(conn, "grp-1", today - timedelta(days=5), [])

    deleted = cleanup_old_snapshots(conn, keep_days=30)
    assert deleted == 1  # удалился только столетний

    assert get_snapshot(conn, "grp-1", today) is not None
    assert get_snapshot(conn, "grp-1", today - timedelta(days=100)) is None
    assert get_snapshot(conn, "grp-1", today - timedelta(days=5)) is not None


def test_snapshot_date_accepts_string(conn):
    """Дата может быть передана как строка 'YYYY-MM-DD'."""
    save_snapshot(conn, "grp-1", "2026-09-12", [])
    assert get_snapshot(conn, "grp-1", "2026-09-12") is not None
    assert get_snapshot(conn, "grp-1", date(2026, 9, 12)) is not None


# ---------- Кэш справочников ----------

def test_save_get_base_info(conn):
    data = {"divisions": [{"id": "d1", "name": "SP2"}]}
    save_base_info(conn, data)
    assert get_base_info(conn) == data


def test_base_info_age_hours_none_when_empty(conn):
    assert base_info_age_hours(conn) is None


def test_base_info_age_hours_recent(conn):
    save_base_info(conn, {"x": 1})
    age = base_info_age_hours(conn)
    assert age is not None
    assert age < 0.1  # меньше 6 минут


def test_base_info_overwrites(conn):
    save_base_info(conn, {"v": 1})
    save_base_info(conn, {"v": 2})
    assert get_base_info(conn) == {"v": 2}


# ---------- Лог уведомлений ----------

def test_log_and_last_notification(conn):
    log_notification(conn, "grp-1", date(2026, 9, 12), "diff", {"changes": 1})
    last = last_notification(conn, "grp-1", date(2026, 9, 12))
    assert last is not None
    assert last["kind"] == "diff"
    assert "changes" in last["payload"]


def test_last_notification_returns_latest(conn):
    log_notification(conn, "grp-1", date(2026, 9, 12), "diff", {})
    log_notification(conn, "grp-1", date(2026, 9, 12), "full", {})
    last = last_notification(conn, "grp-1", date(2026, 9, 12))
    assert last["kind"] == "full"


def test_last_notification_missing(conn):
    assert last_notification(conn, "grp-x", date(2026, 9, 12)) is None


def test_last_notification_isolated_by_group(conn):
    log_notification(conn, "grp-1", date(2026, 9, 12), "diff", {})
    log_notification(conn, "grp-2", date(2026, 9, 12), "full", {})
    assert last_notification(conn, "grp-1", date(2026, 9, 12))["kind"] == "diff"
    assert last_notification(conn, "grp-2", date(2026, 9, 12))["kind"] == "full"


# ---------- Потокобезопасность ----------

def test_concurrent_writes_do_not_crash(tmp_path):
    """10 потоков пишут 50 снимков — не падаем и не теряем данные."""
    import threading
    c = init_db(tmp_path / "concurrent.db")

    errors = []

    def worker(i):
        try:
            for j in range(5):
                save_snapshot(
                    c, f"grp-{i}", date(2026, 9, 12 + j),
                    [{"number": j}],
                )
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == []
    rows = c.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()
    assert rows["n"] == 50
    c.close()


def test_setting_preserves_string_type(conn):
    """Строка '419' остаётся строкой, не превращается в число."""
    set_setting(conn, "group_name", "419")
    value = get_setting(conn, "group_name")
    assert value == "419"
    assert isinstance(value, str)


def test_setting_preserves_int_type(conn):
    """Число 419 остаётся числом."""
    set_setting(conn, "interval", 30)
    value = get_setting(conn, "interval")
    assert value == 30
    assert isinstance(value, int)


def test_setting_preserves_dict_type(conn):
    set_setting(conn, "cfg", {"a": 1, "b": [2, 3]})
    assert get_setting(conn, "cfg") == {"a": 1, "b": [2, 3]}


def test_setting_preserves_none(conn):
    set_setting(conn, "peer", None)
    assert get_setting(conn, "peer") is None


def test_setting_handles_non_json_garbage(conn):
    """Если в БД лежит не-JSON — не падаем, возвращаем как есть."""
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("broken", "not a json {{{"),
        )
    # не должно упасть
    value = get_setting(conn, "broken", "default")
    assert value == "not a json {{{" or value == "default"