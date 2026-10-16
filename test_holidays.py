"""Тесты для holidays.py."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import holidays


@pytest.fixture(autouse=True)
def isolated_holidays_file(tmp_path, monkeypatch):
    """Изолирует holidays.json — свой файл на каждый тест."""
    target = tmp_path / "holidays.json"
    monkeypatch.setattr(holidays, "HOLIDAYS_PATH", target)
    holidays.reload()
    yield target
    holidays.reload()


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ============================================================
# Базовые случаи
# ============================================================


def test_get_holiday_specific(isolated_holidays_file):
    _write_json(isolated_holidays_file, {"yearly": [], "specific": [{"date": "2026-10-05", "name": "День учителя"}]})
    holidays.reload()
    assert holidays.get_holiday(date(2026, 10, 5)) == "День учителя"


def test_get_holiday_yearly(isolated_holidays_file):
    _write_json(isolated_holidays_file, {"yearly": [{"date": "10-05", "name": "День учителя"}], "specific": []})
    holidays.reload()
    # Год в файле не указан — модуль подставляет текущий.
    # Проверяем, что для текущего года праздник находится.
    today = date.today()
    assert holidays.get_holiday(date(today.year, 10, 5)) == "День учителя"


def test_get_holiday_returns_none_for_normal_day(isolated_holidays_file):
    _write_json(isolated_holidays_file, {"yearly": [{"date": "10-05", "name": "День учителя"}], "specific": []})
    holidays.reload()
    today = date.today()
    assert holidays.get_holiday(date(today.year, 6, 15)) is None


def test_is_holiday_true(isolated_holidays_file):
    _write_json(isolated_holidays_file, {"yearly": [{"date": "05-09", "name": "День Победы"}], "specific": []})
    holidays.reload()
    today = date.today()
    is_hol, name = holidays.is_holiday(date(today.year, 5, 9))
    assert is_hol is True
    assert name == "День Победы"


def test_is_holiday_false(isolated_holidays_file):
    _write_json(isolated_holidays_file, {"yearly": [], "specific": []})
    holidays.reload()
    is_hol, name = holidays.is_holiday(date.today())
    assert is_hol is False
    assert name == ""


# ============================================================
# Граничные случаи
# ============================================================


def test_missing_file(isolated_holidays_file):
    """Файла нет — всё работает, но всё False."""
    # Не создаём файл
    holidays.reload()
    is_hol, name = holidays.is_holiday(date(2026, 10, 5))
    assert is_hol is False
    assert name == ""


def test_broken_json(isolated_holidays_file):
    """Битый JSON — не падаем."""
    isolated_holidays_file.write_text("{not valid json", encoding="utf-8")
    holidays.reload()
    is_hol, _ = holidays.is_holiday(date(2026, 10, 5))
    assert is_hol is False


def test_invalid_date_in_file(isolated_holidays_file):
    """Кривая дата в файле — пропускается, остальные работают."""
    _write_json(
        isolated_holidays_file,
        {"yearly": [{"date": "не-дата", "name": "битый"}, {"date": "10-05", "name": "День учителя"}], "specific": []},
    )
    holidays.reload()
    today = date.today()
    assert holidays.get_holiday(date(today.year, 10, 5)) == "День учителя"


def test_impossible_date_yearly_skipped(isolated_holidays_file):
    """32 февраля — пропускается без падения."""
    _write_json(isolated_holidays_file, {"yearly": [{"date": "02-32", "name": "Не бывает"}], "specific": []})
    holidays.reload()
    # Любая дата — не праздник
    assert holidays.get_holiday(date.today()) is None


def test_specific_overrides_yearly(isolated_holidays_file):
    """Specific-запись приоритетнее yearly для той же даты."""
    today = date.today()
    _write_json(
        isolated_holidays_file,
        {
            "yearly": [{"date": "10-05", "name": "Общий день учителя"}],
            "specific": [{"date": f"{today.year}-10-05", "name": "Специфичный день"}],
        },
    )
    holidays.reload()
    # Прямая проверка сначала идёт в кэш (specific), потом fallback (yearly)
    name = holidays.get_holiday(date(today.year, 10, 5))
    # В кэше specific, значит вернётся он
    assert name == "Специфичный день"


def test_reload_picks_up_changes(isolated_holidays_file):
    _write_json(isolated_holidays_file, {"yearly": [], "specific": []})
    holidays.reload()
    today = date.today()
    assert holidays.get_holiday(date(today.year, 10, 5)) is None

    # Меняем файл и перезагружаем
    _write_json(isolated_holidays_file, {"yearly": [{"date": "10-05", "name": "День учителя"}], "specific": []})
    holidays.reload()
    assert holidays.get_holiday(date(today.year, 10, 5)) == "День учителя"
