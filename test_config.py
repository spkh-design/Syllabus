"""Тесты для config.py.

Тестируем чистые функции: _parse_admin_ids, is_admin_id.
Сам модуль при импорте читает .env и может упасть, если VK_TOKEN пуст —
поэтому в тестах работаем с функциями, не с константами.
"""

from __future__ import annotations

import pytest

from config import _parse_admin_ids


# ---------- _parse_admin_ids ----------

def test_parse_admin_ids_empty():
    assert _parse_admin_ids("") == []


def test_parse_admin_ids_single():
    assert _parse_admin_ids("123") == [123]


def test_parse_admin_ids_multiple():
    assert _parse_admin_ids("123,456,789") == [123, 456, 789]


def test_parse_admin_ids_whitespace():
    """Пробелы вокруг ID игнорируются."""
    assert _parse_admin_ids(" 123 , 456 ,789 ") == [123, 456, 789]


def test_parse_admin_ids_empty_chunks():
    """Пустые элементы (двойные запятые) пропускаются."""
    assert _parse_admin_ids("123,,456") == [123, 456]
    assert _parse_admin_ids(",123,") == [123]


def test_parse_admin_ids_non_numeric_skipped(caplog):
    """Нечисловые элементы пропускаются с предупреждением."""
    result = _parse_admin_ids("123,abc,456")
    assert result == [123, 456]
    assert "пропускаю нечисловое" in caplog.text.lower() or \
           "abc" in caplog.text


def test_parse_admin_ids_only_commas():
    assert _parse_admin_ids(",,,") == []


def test_is_admin_id_true(monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_USER_IDS", [123, 456])
    assert config.is_admin_id(123) is True
    assert config.is_admin_id(789) is False


def test_is_admin_id_empty_list(monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_USER_IDS", [])
    assert config.is_admin_id(123) is False