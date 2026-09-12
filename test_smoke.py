"""Smoke-тесты: проверяют реальный API колледжа.

Запускать ТОЛЬКО вручную: pytest -m smoke
НЕ в CI, НЕ в pre-commit — бережём чужой сервер.
"""

from __future__ import annotations

from datetime import date

import pytest

import schedule_api


pytestmark = pytest.mark.smoke


def test_real_index_endpoint_responds():
    """Справочники отдаются и содержат ожидаемые разделы."""
    data = schedule_api.fetch_base_info()
    assert "divisions" in data
    assert "groups" in data
    assert "teachers" in data
    assert len(data["divisions"]) == 5
    assert len(data["groups"]) > 100


def test_real_schedule_endpoint_responds():
    """Расписание на дату отдаётся."""
    payload = schedule_api.fetch_schedule(date(2026, 9, 8))
    assert payload["success"] is True
    assert "schedule" in payload


def test_real_group_lookup():
    """Известная группа (СП-4 / 419) находится."""
    base = schedule_api.fetch_base_info()
    group = schedule_api.find_group(base, "419", "СП-4")
    assert group is not None
    assert group["name"] == "419"


def test_real_group_419_in_sp4():
    """Информативный тест: группа 419 должна быть в СП-4.

    Если она переехала или расформирована — этот тест покажет,
    куда смотреть. Падать не должен, только сообщать.
    """
    base = schedule_api.fetch_base_info()
    group = schedule_api.find_group(base, "419", "СП-4")

    if group is None:
        candidates = [g for g in base["groups"] if "419" in g["name"]]
        print(f"\n⚠️  Группа 419 в СП-4 не найдена.")
        print(f"    Групп с '419' в названии: {len(candidates)}")
        for g in candidates[:5]:
            div = next(
                (d for d in base["divisions"] if d["id"] == g["division"]),
                None,
            )
            print(f"    - {g['name']} / {div['name'] if div else '???'}")
        pytest.skip("Группа 419 в СП-4 отсутствует — см. лог выше")
    else:
        print(f"\n✅ Группа 419 в СП-4 найдена: id={group['id']}")