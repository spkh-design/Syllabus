"""Тесты для schedule_api.py.

Все HTTP-запросы замоканы — реальный API не дёргается.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch, MagicMock

import pytest

from schedule_api import (
    ScheduleAPIError,
    build_lookup,
    find_group,
    fetch_base_info,
    fetch_schedule,
    get_group_lessons,
    get_week_lessons,
)


# ---------- Фикстуры: мини-версии ответов API ----------

@pytest.fixture
def base_info():
    """Мини-справочник: 2 подразделения, 2 группы, 2 препода, 2 дисциплины."""
    return {
        "divisions": [
            {"name": "(СП-2) Отделение строительства", "id": "div-sp2"},
            {"name": "(СП-3) Отделение сферы услуг", "id": "div-sp3"},
            {"name": "(СП-4) Энергетическое отделение", "id": "div-sp4"},
        ],
        "groups": [
            {"name": "419", "id": "grp-419", "division": "div-sp4", "curse": 3},
            {"name": "419", "id": "grp-419-sp2", "division": "div-sp2", "curse": 3},  # вернуть
            {"name": "419", "id": "grp-419-sp3", "division": "div-sp3", "curse": 3},
            {"name": "301", "id": "grp-301", "division": "div-sp2", "curse": 4},
        ],
        "teachers": [
            {"name": "Дубров Никита Александрович", "id": "t-dubrov"},
            {"name": "Кизилова Евгения Александровна", "id": "t-kizilova"},
        ],
        "disciplines": [
            {"name": "Проектирование и разработка веб-приложений", "id": "d-web"},
            {"name": "Тестирование информационных систем", "id": "d-test"},
        ],
        "lesson_Types": [
            {"name": "Лекция", "id": "lt-lec"},
            {"name": "Практическая работа", "id": "lt-prac"},
        ],
        "audithories": [
            {"name": "Кабинет №43", "short_name": "43", "id": "a-43"},
            {"name": "Кабинет №45", "short_name": "45", "id": "a-45"},
            {"name": "Дистанционное занятие", "short_name": "Дистант", "id": "a-dist"},
        ],
        "territories": [
            {"name": "(СП-2) Отделение строительства", "id": "ter-sp2"},
            {"name": "(СП-3) Отделение сферы услуг", "id": "ter-sp3"},
            {"name": "(СП-4) Энергетическое отделение", "id": "ter-sp4"},
        ],
    }


@pytest.fixture
def schedule_payload():
    """Мини-расписание: 2 пары группы 419 на 12.09.2026."""
    ts = int(datetime(2026, 9, 12).timestamp() * 1000)
    return {
        "success": True,
        "schedule": {
            "error": "",
            "data": {
                "schedule": [
                    {
                        "date": ts,
                        "lessons": [
                            {
                                "territory": "ter-sp4",
                                "discipline": "d-web",
                                "teacher": "t-dubrov",
                                "auditoria": "a-43",
                                "lesson_type": "lt-lec",
                                "number_lesson": 1,
                                "subgroup": 0,
                                "group": "grp-419",
                            },
                            {
                                "territory": "ter-sp4",
                                "discipline": "d-test",
                                "teacher": "t-kizilova",
                                "auditoria": "a-45",
                                "lesson_type": "lt-lec",
                                "number_lesson": 2,
                                "subgroup": 0,
                                "group": "grp-419",
                            },
                            {
                                # Чужая группа — не должна попасть в выборку
                                "territory": "ter-sp4",
                                "discipline": "d-web",
                                "teacher": "t-dubrov",
                                "auditoria": "a-43",
                                "lesson_type": "lt-lec",
                                "number_lesson": 1,
                                "subgroup": 0,
                                "group": "grp-301",
                            },
                        ],
                    }
                ]
            },
        },
    }


# ---------- build_lookup ----------

def test_build_lookup_maps_all_entities(base_info):
    lookup = build_lookup(base_info)
    #assert lookup[]
    assert lookup["teachers"]["t-dubrov"] == "Дубров Никита Александрович"
    assert lookup["disciplines"]["d-web"] == "Проектирование и разработка веб-приложений"
    assert lookup["auditoria"]["a-43"] == "43"
    assert lookup["auditoria"]["a-dist"] == "Дистант"
    assert lookup["lesson_types"]["lt-lec"] == "Лекция"
    assert lookup["groups"]["grp-419"] == "419"


def test_build_lookup_handles_empty_base():
    lookup = build_lookup({})
    for key in ("divisions", "groups", "teachers", "disciplines",
                "lesson_types", "auditoria", "territories"):
        assert lookup[key] == {}


def test_build_lookup_auditoria_prefers_short_name(base_info):
    """Если у аудитории есть short_name — берём его, а не name."""
    lookup = build_lookup(base_info)
    assert lookup["auditoria"]["a-43"] == "43"   # короткое
    assert lookup["auditoria"]["a-dist"] == "Дистант"


# ---------- find_group ----------

def test_find_group_by_name_and_division(base_info):
    g = find_group(base_info, "419", "СП-4")
    assert g is not None
    assert g["id"] == "grp-419"
    assert g["division"] == "div-sp4"


def test_find_group_same_name_different_divisions(base_info):
    g2 = find_group(base_info, "419", "СП-2")
    g3 = find_group(base_info, "419", "СП-3")
    assert g2 is not None, "419 не найдена в СП-2"
    assert g3 is not None, "419 не найдена в СП-3"
    assert g2["id"] != g3["id"]


def test_find_group_without_division_returns_first_match(base_info):
    g = find_group(base_info, "419")
    assert g is not None  # вернёт первую найденную


def test_find_group_not_found(base_info):
    assert find_group(base_info, "999", "СП-2") is None


def test_find_group_division_not_found(base_info):
    assert find_group(base_info, "419", "СП-99") is None


def test_find_group_strips_whitespace(base_info):
    assert find_group(base_info, "  419  ", "СП-4") is not None


# ---------- _request: ретраи ----------

def test_fetch_base_info_success(base_info):
    with patch("schedule_api.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "baseInfo": {"data": base_info},
        }
        mock_get.return_value = mock_response

        result = fetch_base_info()
        assert result == base_info
        assert mock_get.call_count == 1


def test_fetch_base_info_retries_on_503():
    with patch("schedule_api.requests.get") as mock_get, \
         patch("schedule_api.time.sleep") as mock_sleep:
        bad = MagicMock(status_code=503, text="Service Unavailable")
        good = MagicMock(status_code=200)
        good.json.return_value = {"success": True, "baseInfo": {"data": {}}}
        mock_get.side_effect = [bad, good]

        result = fetch_base_info()
        assert result == {}
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once()  # была пауза между попытками


def test_fetch_base_info_gives_up_after_max_retries():
    with patch("schedule_api.requests.get") as mock_get, \
         patch("schedule_api.time.sleep"):
        mock_get.return_value = MagicMock(status_code=503, text="down")
        with pytest.raises(ScheduleAPIError):
            fetch_base_info()
        assert mock_get.call_count == 3  # MAX_RETRIES


def test_fetch_base_info_raises_on_4xx():
    """404/403 — не ретраим, сразу ошибка."""
    with patch("schedule_api.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=404, text="not found")
        with pytest.raises(ScheduleAPIError):
            fetch_base_info()
        assert mock_get.call_count == 1  # без ретраев


def test_fetch_base_info_raises_on_success_false():
    with patch("schedule_api.requests.get") as mock_get:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"success": False}
        mock_get.return_value = mock_response
        with pytest.raises(ScheduleAPIError):
            fetch_base_info()


# ---------- fetch_schedule ----------

def test_fetch_schedule_sends_correct_timestamp(schedule_payload):
    with patch("schedule_api.requests.get") as mock_get:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = schedule_payload
        mock_get.return_value = mock_response

        fetch_schedule(date(2026, 9, 12))

        args, kwargs = mock_get.call_args
        params = kwargs["params"]
        expected_ts = int(datetime(2026, 9, 12).timestamp() * 1000)
        assert params["date"] == expected_ts


# ---------- get_group_lessons ----------

def test_get_group_lessons_returns_only_target_group(
    base_info, schedule_payload,
):
    with patch("schedule_api.requests.get") as mock_get:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = schedule_payload
        mock_get.return_value = mock_response

        lessons = get_group_lessons(base_info, "grp-419", date(2026, 9, 12))

    assert len(lessons) == 2
    assert lessons[0]["territory"] == "(СП-4) Энергетическое отделение"
    assert lessons[0]["number"] == 1
    assert lessons[0]["discipline"] == "Проектирование и разработка веб-приложений"
    assert lessons[0]["teacher"] == "Дубров Никита Александрович"
    assert lessons[0]["auditoria"] == "43"
    assert lessons[0]["lesson_type"] == "Лекция"
    assert lessons[1]["number"] == 2
    assert lessons[1]["auditoria"] == "45"


def test_get_group_lessons_sorted_by_number(base_info, schedule_payload):
    with patch("schedule_api.requests.get") as mock_get:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = schedule_payload
        mock_get.return_value = mock_response
        lessons = get_group_lessons(base_info, "grp-419", date(2026, 9, 12))
    numbers = [l["number"] for l in lessons]
    assert numbers == sorted(numbers)


def test_get_group_lessons_empty_when_group_absent(
    base_info, schedule_payload,
):
    with patch("schedule_api.requests.get") as mock_get:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = schedule_payload
        mock_get.return_value = mock_response
        lessons = get_group_lessons(base_info, "grp-nonexistent", date(2026, 9, 12))
    assert lessons == []


def test_get_group_lessons_empty_when_date_mismatch(
    base_info, schedule_payload,
):
    """Если в ответе нет нужного дня — пустой список."""
    with patch("schedule_api.requests.get") as mock_get:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = schedule_payload
        mock_get.return_value = mock_response
        lessons = get_group_lessons(base_info, "grp-419", date(2026, 9, 13))
    assert lessons == []


def test_get_group_lessons_unknown_uuids_render_as_dash(base_info):
    """Неизвестный UUID -> '—' (не падаем)."""
    ts = int(datetime(2026, 9, 12).timestamp() * 1000)
    payload = {
        "success": True,
        "schedule": {"data": {"schedule": [{
            "date": ts,
            "lessons": [{
                "discipline": "unknown", "teacher": "unknown",
                "auditoria": "unknown", "lesson_type": "unknown",
                "number_lesson": 1, "subgroup": 0, "group": "grp-419",
            }],
        }]}},
    }
    with patch("schedule_api.requests.get") as mock_get:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = payload
        mock_get.return_value = mock_response
        lessons = get_group_lessons(base_info, "grp-419", date(2026, 9, 12))
    assert lessons[0]["discipline"] == "—"
    assert lessons[0]["teacher"] == "—"
    assert lessons[0]["auditoria"] == "—"
    assert lessons[0]["lesson_type"] == "—"


# ---------- get_week_lessons ----------

def test_get_week_lessons_returns_seven_days(base_info, schedule_payload):
    with patch("schedule_api.requests.get") as mock_get:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = schedule_payload
        mock_get.return_value = mock_response

        week = get_week_lessons(base_info, "grp-419", date(2026, 9, 7))

    assert len(week) == 7
    dates = sorted(week.keys())
    assert dates[0] == date(2026, 9, 7)
    assert dates[-1] == date(2026, 9, 13)


def test_get_week_lessons_tolerates_individual_failures(base_info):
    """Если одна дата падает — остальные всё равно возвращаются."""
    def fake_get(url, params=None, headers=None, timeout=None):
        # Проваливаем только один день — 09.09.2026
        ts_target = int(datetime(2026, 9, 9).timestamp() * 1000)
        if params and params.get("date") == ts_target:
            raise ScheduleAPIError("boom")
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"success": True,
                                  "schedule": {"data": {"schedule": []}}}
        return resp

    with patch("schedule_api.requests.get", side_effect=fake_get):
        week = get_week_lessons(base_info, "grp-419", date(2026, 9, 7))

    assert len(week) == 7
    assert week[date(2026, 9, 9)] == []


def test_find_group_unknown_division_returns_none(base_info):
    """Если подразделение указано, но не найдено — None (не первая попавшаяся)."""
    assert find_group(base_info, "419", "СП-99") is None


@pytest.mark.parametrize("user_input", [
    "СП-4", "СП4", "сп-4", "сп4", "(СП-4)", "  СП-4  ", "CП 4",
])
def test_find_group_division_normalization(base_info, user_input):
    """Разные формы написания подразделения должны работать одинаково."""
    # Расширим base_info в этом тесте
    base = dict(base_info)
    base["divisions"] = [
        {"name": "(СП-4) Энергетическое отделение", "id": "div-sp4"},
    ]
    base["groups"] = [
        {"name": "419", "id": "grp-419", "division": "div-sp4", "curse": 3},
    ]
    group = find_group(base, "419", user_input)
    assert group is not None
    assert group["id"] == "grp-419"


def test_get_group_lessons_includes_territory(base_info, schedule_payload):
    with patch("schedule_api.requests.get") as mock_get:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = schedule_payload
        mock_get.return_value = mock_response
        lessons = get_group_lessons(base_info, "grp-419", date(2026, 9, 12))
    assert "territory" in lessons[0]