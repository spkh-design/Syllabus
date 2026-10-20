"""Тесты для database.py.

Используют временный файл БД (tmp_path от pytest) — реальная БД не трогается.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from database import (
    base_info_age_hours,
    cleanup_old_snapshots,
    clear_week_announced,
    cleanup_old_announced_weeks,
    get_week_announced,
    set_week_announced,
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
    save_week_image,
    get_week_image,
    delete_week_image,
    cleanup_old_week_images,
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
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"settings", "snapshots", "base_info_cache", "notifications_log"} <= tables


def test_init_db_idempotent(tmp_path):
    """Многократный init не падает и не ломает схему."""
    p = tmp_path / "x.db"
    c1 = init_db(p)
    c1.close()
    c2 = init_db(p)
    c2.close()
    c3 = init_db(p)
    c3.close()


def test_init_db_delete_mode(conn):
    """journal_mode=DELETE — записи идут прямо в bot.db, без WAL-файла."""
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "delete"


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
        {
            "number": 1,
            "subgroup": 0,
            "discipline": "Web",
            "teacher": "Дубров",
            "auditoria": "43",
            "lesson_type": "Лекция",
        }
    ]
    h = save_snapshot(conn, "grp-1", date(2026, 9, 12), lessons)
    assert isinstance(h, str) and len(h) == 64  # sha256 hex

    snap = get_snapshot(conn, "grp-1", date(2026, 9, 12))
    assert snap is not None
    assert snap["hash"] == h
    assert snap["lessons"] == lessons


def test_snapshot_hash_is_stable(conn):
    """Один и тот же снимок даёт один и тот же хеш."""
    lessons = [{"number": 1, "subgroup": 0, "discipline": "X", "teacher": "Y", "auditoria": "1", "lesson_type": "L"}]
    h1 = save_snapshot(conn, "grp-1", date(2026, 9, 12), lessons)
    h2 = save_snapshot(conn, "grp-1", date(2026, 9, 12), lessons)
    assert h1 == h2


def test_snapshot_hash_changes_on_content(conn):
    l1 = [{"number": 1, "subgroup": 0, "discipline": "A", "teacher": "T", "auditoria": "1", "lesson_type": "L"}]
    l2 = [{"number": 1, "subgroup": 0, "discipline": "B", "teacher": "T", "auditoria": "1", "lesson_type": "L"}]
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
    """Удаляются снимки старше 30 дней от СЕГОДНЯ (реальной даты)."""
    today = datetime.now().date()
    # Свежая неделя
    set_week_announced(conn, "grp-1", today, is_full=True)
    # Старая — 100 дней назад
    set_week_announced(conn, "grp-1", today - timedelta(days=100), is_full=True)
    # Ещё одна свежая
    set_week_announced(conn, "grp-1", today - timedelta(days=5), is_full=True)

    deleted = cleanup_old_announced_weeks(conn, keep_days=30)
    assert deleted == 1
    assert get_week_announced(conn, "grp-1", today) is not None
    assert get_week_announced(conn, "grp-1", today - timedelta(days=100)) is None
    assert get_week_announced(conn, "grp-1", today - timedelta(days=5)) is not None


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
                save_snapshot(c, f"grp-{i}", date(2026, 9, 12 + j), [{"number": j}])
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

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
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("broken", "not a json {{{"))
    # не должно упасть
    value = get_setting(conn, "broken", "default")
    assert value == "not a json {{{" or value == "default"


# ---------- announced_weeks ----------


def test_set_and_get_week_announced(conn):
    set_week_announced(conn, "grp-1", date(2026, 9, 14), is_full=True)
    row = get_week_announced(conn, "grp-1", date(2026, 9, 14))
    assert row is not None
    assert row["is_full"] is True
    assert row["announced_at"]


def test_get_week_announced_missing(conn):
    assert get_week_announced(conn, "grp-1", date(2026, 9, 14)) is None


def test_set_week_announced_overwrites(conn):
    """Повторный вызов обновляет запись."""
    set_week_announced(conn, "grp-1", date(2026, 9, 14), is_full=False)
    set_week_announced(conn, "grp-1", date(2026, 9, 14), is_full=True)
    row = get_week_announced(conn, "grp-1", date(2026, 9, 14))
    assert row["is_full"] is True


def test_week_announced_different_groups(conn):
    """Записи для разных групп независимы."""
    set_week_announced(conn, "grp-1", date(2026, 9, 14), is_full=True)
    set_week_announced(conn, "grp-2", date(2026, 9, 14), is_full=False)
    assert get_week_announced(conn, "grp-1", date(2026, 9, 14))["is_full"] is True
    assert get_week_announced(conn, "grp-2", date(2026, 9, 14))["is_full"] is False


def test_week_announced_different_weeks(conn):
    """Записи для разных недель независимы."""
    set_week_announced(conn, "grp-1", date(2026, 9, 14), is_full=True)
    set_week_announced(conn, "grp-1", date(2026, 9, 21), is_full=True)
    assert get_week_announced(conn, "grp-1", date(2026, 9, 14)) is not None
    assert get_week_announced(conn, "grp-1", date(2026, 9, 21)) is not None
    assert get_week_announced(conn, "grp-1", date(2026, 9, 28)) is None


def test_week_announced_accepts_string(conn):
    set_week_announced(conn, "grp-1", "2026-09-14", is_full=True)
    assert get_week_announced(conn, "grp-1", "2026-09-14") is not None
    assert get_week_announced(conn, "grp-1", date(2026, 9, 14)) is not None


def test_clear_week_announced_specific(conn):
    set_week_announced(conn, "grp-1", date(2026, 9, 14), is_full=True)
    set_week_announced(conn, "grp-1", date(2026, 9, 21), is_full=True)
    deleted = clear_week_announced(conn, "grp-1", date(2026, 9, 14))
    assert deleted == 1
    assert get_week_announced(conn, "grp-1", date(2026, 9, 14)) is None
    assert get_week_announced(conn, "grp-1", date(2026, 9, 21)) is not None


def test_clear_week_announced_all_for_group(conn):
    set_week_announced(conn, "grp-1", date(2026, 9, 14), is_full=True)
    set_week_announced(conn, "grp-1", date(2026, 9, 21), is_full=True)
    set_week_announced(conn, "grp-2", date(2026, 9, 14), is_full=True)

    deleted = clear_week_announced(conn, "grp-1")
    assert deleted == 2
    assert get_week_announced(conn, "grp-1", date(2026, 9, 14)) is None
    assert get_week_announced(conn, "grp-1", date(2026, 9, 21)) is None
    # grp-2 не тронули
    assert get_week_announced(conn, "grp-2", date(2026, 9, 14)) is not None


def test_cleanup_old_announced_weeks(conn):
    today = datetime.now().date()
    # Свежая неделя
    set_week_announced(conn, "grp-1", today, is_full=True)
    # Старая — 100 дней назад
    set_week_announced(conn, "grp-1", today - timedelta(days=100), is_full=True)
    # Ещё одна свежая
    set_week_announced(conn, "grp-1", today - timedelta(days=5), is_full=True)

    deleted = cleanup_old_announced_weeks(conn, keep_days=30)
    assert deleted == 1
    assert get_week_announced(conn, "grp-1", today) is not None
    assert get_week_announced(conn, "grp-1", today - timedelta(days=100)) is None
    assert get_week_announced(conn, "grp-1", today - timedelta(days=5)) is not None


def test_week_announced_full_flag_persists(conn):
    """is_full сохраняется и читается как bool."""
    set_week_announced(conn, "grp-1", date(2026, 9, 14), is_full=False)
    row = get_week_announced(conn, "grp-1", date(2026, 9, 14))
    assert isinstance(row["is_full"], bool)
    assert row["is_full"] is False


def test_announced_weeks_persist_after_reconnect(conn, tmp_path):
    """Данные переживают переподключение к БД."""
    set_week_announced(conn, "grp-1", date(2026, 9, 14), is_full=True)
    conn.close()

    # Переоткрываем ту же БД
    from database import init_db

    conn2 = init_db(tmp_path / "test.db")
    row = get_week_announced(conn2, "grp-1", date(2026, 9, 14))
    assert row is not None
    assert row["is_full"] is True
    conn2.close()


# ---------- week_images ----------


def test_save_and_get_week_image(conn):
    png = b"\x89PNG\r\n\x1a\nfake-image-bytes"
    save_week_image(conn, "grp-1", date(2026, 9, 21), png)
    assert get_week_image(conn, "grp-1", date(2026, 9, 21)) == png


def test_get_week_image_missing(conn):
    assert get_week_image(conn, "grp-1", date(2026, 9, 21)) is None


def test_save_week_image_overwrites(conn):
    save_week_image(conn, "grp-1", date(2026, 9, 21), b"old")
    save_week_image(conn, "grp-1", date(2026, 9, 21), b"new")
    assert get_week_image(conn, "grp-1", date(2026, 9, 21)) == b"new"


def test_delete_week_image(conn):
    save_week_image(conn, "grp-1", date(2026, 9, 21), b"x")
    delete_week_image(conn, "grp-1", date(2026, 9, 21))
    assert get_week_image(conn, "grp-1", date(2026, 9, 21)) is None


def test_cleanup_old_week_images(conn):
    """Динамические даты — не зависим от реальной сегодняшней даты."""
    today = datetime.now().date()
    save_week_image(conn, "grp-1", today, b"new")
    save_week_image(conn, "grp-1", today - timedelta(days=100), b"old")
    deleted = cleanup_old_week_images(conn, keep_days=30)
    assert deleted == 1
    assert get_week_image(conn, "grp-1", today) is not None
    assert get_week_image(conn, "grp-1", today - timedelta(days=100)) is None


# ============================================================
# groups
# ============================================================


def test_add_group_creates(conn):
    from database import add_group, get_group

    add_group(conn, 2000000002, "grp-419", "419", "СП-4")
    g = get_group(conn, 2000000002)
    assert g is not None
    assert g["group_uuid"] == "grp-419"
    assert g["group_name"] == "419"
    assert g["division"] == "СП-4"
    assert g["curator_id"] is None
    assert g["is_active"] is True


def test_add_group_overwrites(conn):
    from database import add_group, get_group

    add_group(conn, 2000000002, "grp-419", "419", "СП-4")
    add_group(conn, 2000000002, "grp-301", "301", "СП-2")
    g = get_group(conn, 2000000002)
    assert g["group_uuid"] == "grp-301"
    assert g["group_name"] == "301"


def test_add_group_preserves_curator(conn):
    from database import add_group, get_group, set_group_curator

    add_group(conn, 2000000002, "grp-419", "419", "СП-4")
    set_group_curator(conn, 2000000002, 123456)
    # Повторная регистрация без куратора — куратор не должен затереться
    add_group(conn, 2000000002, "grp-419", "419", "СП-4", curator_id=None)
    g = get_group(conn, 2000000002)
    assert g["curator_id"] == 123456


def test_get_group_missing(conn):
    from database import get_group

    assert get_group(conn, 999) is None


def test_get_all_groups(conn):
    from database import add_group, get_all_groups

    add_group(conn, 2000000002, "grp-419", "419", "СП-4")
    add_group(conn, 2000000003, "grp-301", "301", "СП-2")
    groups = get_all_groups(conn)
    assert len(groups) == 2


def test_get_all_groups_only_active(conn):
    from database import add_group, get_all_groups, set_group_active

    add_group(conn, 2000000002, "grp-419", "419", "СП-4")
    add_group(conn, 2000000003, "grp-301", "301", "СП-2")
    set_group_active(conn, 2000000003, False)
    assert len(get_all_groups(conn)) == 1
    assert len(get_all_groups(conn, only_active=False)) == 2


def test_remove_group(conn):
    from database import add_group, get_group, remove_group

    add_group(conn, 2000000002, "grp-419", "419", "СП-4")
    remove_group(conn, 2000000002)
    assert get_group(conn, 2000000002) is None


def test_set_group_curator(conn):
    from database import add_group, get_group, set_group_curator

    add_group(conn, 2000000002, "grp-419", "419", "СП-4")
    set_group_curator(conn, 2000000002, 555)
    assert get_group(conn, 2000000002)["curator_id"] == 555
    set_group_curator(conn, 2000000002, None)
    assert get_group(conn, 2000000002)["curator_id"] is None


def test_set_group_active(conn):
    from database import add_group, get_group, set_group_active

    add_group(conn, 2000000002, "grp-419", "419", "СП-4")
    set_group_active(conn, 2000000002, False)
    assert get_group(conn, 2000000002)["is_active"] is False


# ============================================================
# user_sessions
# ============================================================


def test_set_get_user_session(conn):
    from database import set_user_session, get_user_session

    set_user_session(conn, 12345, 2000000002)
    assert get_user_session(conn, 12345) == 2000000002


def test_user_session_none(conn):
    from database import get_user_session

    assert get_user_session(conn, 999) is None


def test_user_session_overwrites(conn):
    from database import set_user_session, get_user_session

    set_user_session(conn, 12345, 2000000002)
    set_user_session(conn, 12345, 2000000003)
    assert get_user_session(conn, 12345) == 2000000003


def test_clear_user_session(conn):
    from database import set_user_session, get_user_session, clear_user_session

    set_user_session(conn, 12345, 2000000002)
    clear_user_session(conn, 12345)
    assert get_user_session(conn, 12345) is None


# ============================================================
# chat_messages
# ============================================================


def test_save_chat_message(conn):
    from database import save_chat_message, get_chat_message

    save_chat_message(conn, 2000000002, 100, 111, "привет", [])
    m = get_chat_message(conn, 2000000002, 100)
    assert m is not None
    assert m["text"] == "привет"
    assert m["from_id"] == 111


def test_save_chat_message_idempotent(conn):
    from database import save_chat_message, get_chat_message

    save_chat_message(conn, 2000000002, 100, 111, "первая", [])
    save_chat_message(conn, 2000000002, 100, 111, "вторая", [])
    m = get_chat_message(conn, 2000000002, 100)
    assert m["text"] == "первая"  # первая версия не перезаписана


def test_get_chat_messages_since(conn):
    from database import save_chat_message, get_chat_messages_since

    save_chat_message(conn, 2000000002, 100, 111, "старое", [])
    save_chat_message(conn, 2000000002, 101, 111, "новое", [])
    # Вручную сдвинем время первого
    conn.execute("UPDATE chat_messages SET sent_at = '2020-01-01T00:00:00' " "WHERE message_id = 100")
    conn.commit()
    msgs = get_chat_messages_since(conn, 2000000002, "2024-01-01T00:00:00")
    assert len(msgs) == 1
    assert msgs[0]["text"] == "новое"


def test_count_chat_messages_since(conn):
    from database import save_chat_message, count_chat_messages_since

    save_chat_message(conn, 2000000002, 100, 111, "a", [])
    save_chat_message(conn, 2000000002, 101, 222, "b", [])
    save_chat_message(conn, 2000000002, 102, 333, "c", [])
    n = count_chat_messages_since(conn, 2000000002, "2020-01-01", "2030-01-01")
    assert n == 3


def test_count_chat_messages_exclude_from_id(conn):
    from database import save_chat_message, count_chat_messages_since

    save_chat_message(conn, 2000000002, 100, 111, "a", [])
    save_chat_message(conn, 2000000002, 101, 999, "b", [])  # бот
    n = count_chat_messages_since(conn, 2000000002, "2020-01-01", "2030-01-01", exclude_from_id=999)
    assert n == 1


def test_mark_chat_message_ocr_done(conn):
    from database import save_chat_message, get_chat_message, mark_chat_message_ocr_done

    save_chat_message(conn, 2000000002, 100, 111, "", [{"type": "photo"}])
    assert get_chat_message(conn, 2000000002, 100)["ocr_done"] is False
    mark_chat_message_ocr_done(conn, 2000000002, 100)
    assert get_chat_message(conn, 2000000002, 100)["ocr_done"] is True


def test_cleanup_old_chat_messages(conn):
    from database import save_chat_message, cleanup_old_chat_messages

    save_chat_message(conn, 2000000002, 100, 111, "старое", [])
    save_chat_message(conn, 2000000002, 101, 111, "новое", [])
    conn.execute("UPDATE chat_messages SET sent_at = '2020-01-01T00:00:00' " "WHERE message_id = 100")
    conn.commit()
    deleted = cleanup_old_chat_messages(conn, keep_days=30)
    assert deleted == 1


# ============================================================
# bell_schedules
# ============================================================


def test_save_bell_schedule(conn):
    from database import save_bell_schedule, get_bell_schedule

    save_bell_schedule(
        conn, 2000000002, "2026-10-05", 500, 111, image_bytes=b"PNG...", caption="звонки", ocr_text="РАСПИСАНИЕ ЗВОНОК"
    )
    bs = get_bell_schedule(conn, 2000000002, "2026-10-05")
    assert bs is not None
    assert bs["target_date"] == "2026-10-05"
    assert bs["image_bytes"] == b"PNG..."
    assert bs["sent_at"] is None


def test_save_bell_schedule_overwrites(conn):
    from database import save_bell_schedule, get_bell_schedule, mark_bell_schedule_sent

    save_bell_schedule(conn, 2000000002, "2026-10-05", 500, 111)
    mark_bell_schedule_sent(conn, 2000000002, "2026-10-05")
    assert get_bell_schedule(conn, 2000000002, "2026-10-05")["sent_at"] is not None
    # Новая версия — sent_at должен сброситься
    save_bell_schedule(conn, 2000000002, "2026-10-05", 501, 111)
    assert get_bell_schedule(conn, 2000000002, "2026-10-05")["sent_at"] is None


def test_get_bell_schedule_missing(conn):
    from database import get_bell_schedule

    assert get_bell_schedule(conn, 2000000002, "2026-10-05") is None


def test_mark_bell_schedule_sent(conn):
    from database import save_bell_schedule, mark_bell_schedule_sent, get_bell_schedule

    save_bell_schedule(conn, 2000000002, "2026-10-05", 500, 111)
    mark_bell_schedule_sent(conn, 2000000002, "2026-10-05")
    assert get_bell_schedule(conn, 2000000002, "2026-10-05")["sent_at"] is not None


def test_delete_bell_schedule(conn):
    from database import save_bell_schedule, delete_bell_schedule, get_bell_schedule

    save_bell_schedule(conn, 2000000002, "2026-10-05", 500, 111)
    delete_bell_schedule(conn, 2000000002, "2026-10-05")
    assert get_bell_schedule(conn, 2000000002, "2026-10-05") is None


def test_cleanup_old_bell_schedules(conn):
    from database import save_bell_schedule, cleanup_old_bell_schedules, get_bell_schedule

    # Прошлое — должно удалиться
    save_bell_schedule(conn, 2000000002, "2020-01-01", 500, 111)
    # Будущее — должно остаться
    save_bell_schedule(conn, 2000000002, "2099-12-31", 501, 111)
    deleted = cleanup_old_bell_schedules(conn)
    assert deleted == 1
    assert get_bell_schedule(conn, 2000000002, "2020-01-01") is None
    assert get_bell_schedule(conn, 2000000002, "2099-12-31") is not None


# ============================================================
# holiday_pings
# ============================================================


def test_mark_holiday_pinged(conn):
    from database import mark_holiday_pinged, was_holiday_pinged

    mark_holiday_pinged(conn, 2000000002, "2026-10-05")
    assert was_holiday_pinged(conn, 2000000002, "2026-10-05") is True


def test_was_holiday_pinged_false(conn):
    from database import was_holiday_pinged

    assert was_holiday_pinged(conn, 2000000002, "2026-10-05") is False


def test_cleanup_old_holiday_pings(conn):
    from database import mark_holiday_pinged, cleanup_old_holiday_pings

    mark_holiday_pinged(conn, 2000000002, "2020-01-01")
    mark_holiday_pinged(conn, 2000000002, "2099-12-31")
    deleted = cleanup_old_holiday_pings(conn, keep_days=30)
    assert deleted == 1


# ============================================================
# daily cleanup trigger
# ============================================================


def test_should_run_daily_cleanup_no_marker(conn):
    from database import should_run_daily_cleanup

    assert should_run_daily_cleanup(conn) is True


def test_should_run_daily_cleanup_recent(conn):
    from database import should_run_daily_cleanup, mark_daily_cleanup_done

    mark_daily_cleanup_done(conn)
    assert should_run_daily_cleanup(conn) is False


def test_should_run_daily_cleanup_old(conn):
    from database import should_run_daily_cleanup, set_setting

    set_setting(conn, "last_cleanup_at", "2020-01-01T00:00:00")
    assert should_run_daily_cleanup(conn) is True


# ============================================================
# migrate settings → groups
# ============================================================


def test_migrate_settings_to_groups(conn):
    from database import migrate_settings_to_groups, get_group, get_setting

    set_setting(conn, "peer_id", "2000000002")
    set_setting(conn, "group_uuid", "grp-419")
    set_setting(conn, "group_name", "419")
    set_setting(conn, "division", "СП-4")

    result = migrate_settings_to_groups(conn)
    assert result is True

    g = get_group(conn, 2000000002)
    assert g is not None
    assert g["group_uuid"] == "grp-419"
    # Старые ключи пока НЕ удаляем (переходный период)
    assert get_setting(conn, "group_uuid") == "grp-419"


def test_migrate_settings_to_groups_no_data(conn):
    from database import migrate_settings_to_groups

    assert migrate_settings_to_groups(conn) is False


def test_migrate_settings_to_groups_groups_not_empty(conn):
    from database import migrate_settings_to_groups, add_group

    add_group(conn, 2000000002, "grp-419", "419", "СП-4")
    set_setting(conn, "peer_id", "2000000003")
    set_setting(conn, "group_uuid", "grp-301")
    # Уже есть группа — миграция не делается
    assert migrate_settings_to_groups(conn) is False


# ============================================================
# teachers
# ============================================================


def test_add_teacher_creates(conn):
    from database import add_teacher, get_teacher

    add_teacher(conn, 2000000002, "uuid-teacher", "Юнева Наталья Дмитриевна")
    t = get_teacher(conn, 2000000002)
    assert t is not None
    assert t["teacher_uuid"] == "uuid-teacher"
    assert t["teacher_name"] == "Юнева Наталья Дмитриевна"
    assert t["is_active"] is True


def test_add_teacher_removes_group(conn):
    """При добавлении преподавателя в ту же беседу группа удаляется."""
    from database import add_group, add_teacher, get_group, get_teacher

    add_group(conn, 2000000002, "grp-419", "419", "СП-4")
    add_teacher(conn, 2000000002, "uuid-teacher", "Юнева Н.Д.")

    assert get_group(conn, 2000000002) is None
    assert get_teacher(conn, 2000000002) is not None


def test_add_group_removes_teacher(conn):
    """При добавлении группы в ту же беседу преподаватель удаляется."""
    from database import add_group, add_teacher, get_group, get_teacher

    add_teacher(conn, 2000000002, "uuid-teacher", "Юнева Н.Д.")
    add_group(conn, 2000000002, "grp-419", "419", "СП-4")

    assert get_teacher(conn, 2000000002) is None
    assert get_group(conn, 2000000002) is not None


def test_get_all_teachers(conn):
    from database import add_teacher, get_all_teachers

    add_teacher(conn, 2000000002, "uuid-1", "Препод 1")
    add_teacher(conn, 2000000003, "uuid-2", "Препод 2")
    assert len(get_all_teachers(conn)) == 2


def test_remove_teacher(conn):
    from database import add_teacher, get_teacher, remove_teacher

    add_teacher(conn, 2000000002, "uuid-teacher", "Юнева Н.Д.")
    remove_teacher(conn, 2000000002)
    assert get_teacher(conn, 2000000002) is None


def test_get_entity_group(conn):
    from database import add_group, get_entity

    add_group(conn, 2000000002, "grp-419", "419", "СП-4")
    entity = get_entity(conn, 2000000002)
    assert entity is not None
    assert entity["type"] == "group"
    assert entity["group_name"] == "419"


def test_get_entity_teacher(conn):
    from database import add_teacher, get_entity

    add_teacher(conn, 2000000002, "uuid-teacher", "Юнева Н.Д.")
    entity = get_entity(conn, 2000000002)
    assert entity is not None
    assert entity["type"] == "teacher"
    assert entity["teacher_name"] == "Юнева Н.Д."


def test_get_entity_empty(conn):
    from database import get_entity

    assert get_entity(conn, 999) is None
