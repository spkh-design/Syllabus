"""Парсер строки для команд /add_contact и /update_contact."""

from __future__ import annotations

import re
from typing import Any

# Разрешённые ключи
KEYS = {
    "почта": "emails",
    "телеграмм": "telegram",
    "группа телеграмм": "tg_groups",
    "вк": "vk",
    "вк группа": "vk_groups",
    "телефон": "phones",
    "папки": "folders",
    "ссылка max": "max_link",
    "адрес": "address",
}

# Поля, которые могут содержать несколько значений через запятую
LIST_FIELDS = {"emails", "tg_groups", "vk_groups", "phones", "folders"}


def parse_contact_string(raw_text: str) -> tuple[str, dict[str, Any]]:
    """Парсит строку ввода.

    Возвращает:
        (ФИО, dict с ключами из KEYS)
    """
    if not raw_text:
        return "", {}

    # Ищем позицию первого ключевого слова
    # Сначала создаем регулярку для поиска ключей (без учета регистра)
    keys_pattern = "|".join(KEYS.keys())
    first_match = re.search(rf"(?i)\b({keys_pattern}):", raw_text)

    if not first_match:
        # Нет ключей — вся строка это ФИО
        return raw_text.strip(), {}

    fio = raw_text[: first_match.start()].strip()
    rest = raw_text[first_match.start() :]

    # Теперь разбиваем rest по всем ключам
    # Добавляем разделитель, чтобы регулярка сработала на всех
    # Используем lookahead, чтобы не съесть сам ключ
    parts = re.split(rf"(?i)(?=\b({keys_pattern}):)", rest)

    result = {}
    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Отделяем ключ от значения
        m = re.match(rf"(?i)^({keys_pattern}):\s*(.*)", part, re.DOTALL)
        if not m:
            continue

        key_raw = m.group(1).lower()
        value_raw = m.group(2).strip()

        field = KEYS[key_raw]

        if field in LIST_FIELDS:
            # Разбиваем по запятым, убираем пустые
            items = [x.strip() for x in value_raw.split(",") if x.strip()]
            result[field] = items
        else:
            result[field] = value_raw

    return fio, result
