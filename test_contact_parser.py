"""Тесты для contact_parser.py."""

from __future__ import annotations

import pytest

from contact_parser import parse_contact_string


def test_parse_only_fio():
    """Если нет ключевых слов — вся строка это ФИО."""
    fio, data = parse_contact_string("Иванов Иван Иванович")
    assert fio == "Иванов Иван Иванович"
    assert data == {}


def test_parse_fio_and_single_fields():
    text = "Иванов Иван Почта: test@mail.ru Телеграмм: @ivan_vk"
    fio, data = parse_contact_string(text)
    assert fio == "Иванов Иван"
    assert data == {"emails": ["test@mail.ru"], "telegram": "@ivan_vk"}


def test_parse_list_fields():
    text = "Петров Петр Телефон: +79991112233, +78884445566 Папки: https://disk.yandex.ru/d/123, https://drive.google.com/123"
    fio, data = parse_contact_string(text)
    assert fio == "Петров Петр"
    assert data == {
        "phones": ["+79991112233", "+78884445566"],
        "folders": ["https://disk.yandex.ru/d/123", "https://drive.google.com/123"],
    }


def test_parse_case_insensitive_keys():
    text = "Сидоров Сидор ПОЧТА: a@b.c телефон: 123"
    fio, data = parse_contact_string(text)
    assert fio == "Сидоров Сидор"
    assert data == {"emails": ["a@b.c"], "phones": ["123"]}


def test_parse_empty_string():
    fio, data = parse_contact_string("")
    assert fio == ""
    assert data == {}


def test_parse_spaces_and_empty_values():
    """Пустые значения (например, 'Почта:') должны давать пустой список."""
    text = "Иванов Иван Почта: Телефон: 123"
    fio, data = parse_contact_string(text)
    assert fio == "Иванов Иван"
    assert data == {"emails": [], "phones": ["123"]}
