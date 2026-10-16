"""Тесты для bell_detector.py."""

from __future__ import annotations

from datetime import date

import pytest

from bell_detector import BellDetectionResult, detect_bell_schedule, extract_date

# ============================================================
# extract_date
# ============================================================


TODAY = date(2026, 10, 6)  # для тестов без указания года


def test_extract_date_dd_mm_yyyy():
    assert extract_date("05.10.2026", today=TODAY) == date(2026, 10, 5)


def test_extract_date_dd_mm_yy():
    assert extract_date("05.10.26", today=TODAY) == date(2026, 10, 5)


def test_extract_date_d_month_yyyy():
    assert extract_date("5 октября 2026", today=TODAY) == date(2026, 10, 5)


def test_extract_date_d_month_no_year():
    """Без года — используем today.year."""
    assert extract_date("5 октября", today=TODAY) == date(2026, 10, 5)


def test_extract_date_d_month_november():
    assert extract_date("11 ноября 2026", today=TODAY) == date(2026, 11, 11)


def test_extract_date_dd_mm_short():
    """DD.MM без года — текущий год."""
    assert extract_date("05.10", today=TODAY) == date(2026, 10, 5)


def test_extract_date_nothing():
    assert extract_date("расписание звонков", today=TODAY) is None


def test_extract_date_empty():
    assert extract_date("", today=TODAY) is None


def test_extract_date_returns_first():
    """Если несколько дат — возвращаем первую (по позиции в тексте)."""
    text = "05.10.2026 и 06.10.2026"
    assert extract_date(text, today=TODAY) == date(2026, 10, 5)


def test_extract_date_invalid_skipped():
    """Невалидная дата пропускается, берётся следующая валидная."""
    # 32 октября — не бывает, но паттерн словесный
    text = "32 октября 2026 года — 05.11.2026"
    # Первая невалидная (32.10) — отбрасываем, должно вернуть 05.11
    assert extract_date(text, today=TODAY) == date(2026, 11, 5)


def test_extract_date_invalid_dot_skipped():
    """31.02.2026 — невалидно, должно пропустить и найти следующую."""
    text = "31.02.2026 и 05.10.2026"
    # Первая дата — невалидна (31 февраля нет), _DATE_DOT_RE найдёт её,
    # но try/except ValueError её пропустит, дальше пойдёт словесный/короткий.
    # В реальности extract_date для "31.02.2026 и 05.10.2026" вернёт 05.10.2026?
    # Нет — регекс найдёт "31.02.2026" первым, ValueError, потом перейдёт к словам,
    # потом к короткому формату "05.10" → date(2026, 10, 5)
    result = extract_date(text, today=TODAY)
    assert result == date(2026, 10, 5)


# ============================================================
# detect_bell_schedule — негативные
# ============================================================


def test_detect_no_trigger():
    """Нет слова «звонк» — не bell schedule."""
    text = "05.10.2026 будут изменения в расписании"
    r = detect_bell_schedule(text, today=TODAY)
    assert r.is_bell_schedule is False
    assert r.target_date is None


def test_detect_empty():
    r = detect_bell_schedule("", today=TODAY)
    assert r.is_bell_schedule is False


def test_detect_trigger_no_date():
    """Есть «звонк», но даты нет — не детектим."""
    text = "РАСПИСАНИЕ ЗВОНОКОВ будет позже"
    r = detect_bell_schedule(text, today=TODAY)
    assert r.is_bell_schedule is False
    assert r.has_date is False


def test_detect_wrong_format():
    """Дата есть, но нет слова «звонк» — не наше."""
    text = "Расписание занятий на 05.10.2026"
    r = detect_bell_schedule(text, today=TODAY)
    assert r.is_bell_schedule is False


# ============================================================
# detect_bell_schedule — позитивные
# ============================================================


def test_detect_simple_after():
    """«Расписание звонков 05.10.2026» — дата после триггера."""
    r = detect_bell_schedule("Расписание звонков 05.10.2026", today=TODAY)
    assert r.is_bell_schedule is True
    assert r.target_date == date(2026, 10, 5)


def test_detect_simple_before():
    """«05.10.2026 — Расписание звонков» — дата до триггера."""
    r = detect_bell_schedule("05.10.2026 — Расписание звонков", today=TODAY)
    assert r.is_bell_schedule is True
    assert r.target_date == date(2026, 10, 5)


def test_detect_typical_header():
    """Реальный формат из примера с шапкой.

    В шапке документа — «30» сентября 2026 (дата подписи, до триггера).
    После триггера — 05.10.2026 (целевая дата).
    Ожидаем 05.10.2026, а не 30.09.2026.
    """
    text = (
        "АУ «Сургутский политехнический колледж»\n"
        "Заместитель директора\n"
        "А.Л. Минчук\n"
        '"30" сентября 2026 год\n'
        "\n"
        "РАСПИСАНИЕ ЗВОНОКОВ\n"
        "05.10.2026\n"
        "таблица с расписанием звонков"
    )
    r = detect_bell_schedule(text, today=TODAY)
    assert r.is_bell_schedule is True
    assert r.target_date == date(2026, 10, 5)


def test_detect_with_word_month():
    """«Расписание звонков на 11 ноября 2026»."""
    r = detect_bell_schedule("Расписание звонков на 11 ноября 2026", today=TODAY)
    assert r.is_bell_schedule is True
    assert r.target_date == date(2026, 11, 11)


def test_detect_with_short_date():
    """«Расписание звонков на 05.10»."""
    r = detect_bell_schedule("Расписание звонков на 05.10", today=TODAY)
    assert r.is_bell_schedule is True
    assert r.target_date == date(2026, 10, 5)


def test_detect_uppercase():
    """Регистр не важен."""
    r = detect_bell_schedule("РАСПИСАНИЕ ЗВОНОКОВ 05.10.2026", today=TODAY)
    assert r.is_bell_schedule is True


def test_detect_ЗВОНКИ_form():
    """Другая форма: «звонки»."""
    r = detect_bell_schedule("Звонки на 05.10.2026", today=TODAY)
    assert r.is_bell_schedule is True
    assert r.target_date == date(2026, 10, 5)


def test_detect_uses_ocr_text():
    """Текст может прийти в ocr_text, а не в text."""
    r = detect_bell_schedule(text="", ocr_text="РАСПИСАНИЕ ЗВОНОКОВ 05.10.2026", today=TODAY)
    assert r.is_bell_schedule is True
    assert r.target_date == date(2026, 10, 5)


def test_detect_text_and_ocr_combined():
    """Триггер в тексте, дата — в OCR."""
    r = detect_bell_schedule(text="Смотрите, звонки:", ocr_text="05.10.2026", today=TODAY)
    assert r.is_bell_schedule is True
    assert r.target_date == date(2026, 10, 5)


def test_detect_date_outside_window():
    """Дата очень далеко от триггера (>200 символов) — fallback на весь текст."""
    filler = "х" * 250
    text = f"РАСПИСАНИЕ ЗВОНОКОВ{filler}05.10.2026"
    r = detect_bell_schedule(text, today=TODAY)
    # После триггера в окне 200 даты нет. До триггера тоже. Fallback: весь текст → дата найдётся
    assert r.is_bell_schedule is True
    assert r.target_date == date(2026, 10, 5)


def test_detect_ignores_header_date_priority():
    """Дата подписи (до триггера) НЕ должна перебивать дату после триггера."""
    text = (
        '"25" сентября 2026 год\n'  # подпись — до триггера
        "РАСПИСАНИЕ ЗВОНОКОВ\n"
        "05.10.2026\n"  # целевая — после
    )
    r = detect_bell_schedule(text, today=TODAY)
    assert r.target_date == date(2026, 10, 5)
    # Убеждаемся, что не поймали подпись
    assert r.target_date != date(2026, 9, 25)


def test_detect_prefers_user_text_date_over_ocr():
    """Если пользователь напечатал дату — она приоритетнее OCR.

    Реальный кейс: пользователь прислал картинку с РАСПИСАНИЕМ ЗВОНОКОВ
    на 17.10.2026, но OCR прочитал «17» как «07». Текст сообщения —
    «Расписание на 17.10» — должен победить.
    """
    r = detect_bell_schedule(
        text="Расписание на 17.10", ocr_text="РАСПИСАНИЕ ЗВОНОКОВ\n07.10.2026", today=date(2026, 10, 15)  # OCR ошибся
    )
    assert r.is_bell_schedule is True
    assert r.target_date == date(2026, 10, 17)


def test_detect_uses_ocr_date_when_text_has_no_date():
    """Если в тексте даты нет — берём из OCR."""
    r = detect_bell_schedule(
        text="Смотрите, звонки", ocr_text="РАСПИСАНИЕ ЗВОНОКОВ\n17.10.2026", today=date(2026, 10, 15)
    )
    assert r.is_bell_schedule is True
    assert r.target_date == date(2026, 10, 17)


def test_detect_ignores_random_date_in_text_without_schedule_word():
    """Дата в тексте, но без «расписан/звонк» — не подхватываем."""
    r = detect_bell_schedule(
        text="Сегодня 15 октября, холодно", ocr_text="РАСПИСАНИЕ ЗВОНОКОВ\n17.10.2026", today=date(2026, 10, 15)
    )
    # Триггер есть в OCR, даты в тексте нет — берём 17.10 из OCR
    assert r.is_bell_schedule is True
    assert r.target_date == date(2026, 10, 17)


# ============================================================
# OCR-артефакты в датах
# ============================================================


def test_extract_date_fixes_bracket():
    """Tesseract читает «17.10.2026» как «[7.10.2026»."""
    assert extract_date("[7.10.2026", today=TODAY) == date(2026, 10, 17)


def test_extract_date_fixes_pipe():
    """«|7.10.2026» → «17.10.2026»."""
    assert extract_date("|7.10.2026", today=TODAY) == date(2026, 10, 17)


def test_extract_date_fixes_lowercase_l():
    """«l7.10.2026» → «17.10.2026»."""
    assert extract_date("l7.10.2026", today=TODAY) == date(2026, 10, 17)


def test_extract_date_does_not_break_two_digit_day():
    """«[30.09.2026» — скобка перед двумя цифрами не заменяется.

    Тут скобка, скорее всего, настоящая (обрамление), а не OCR-ошибка.
    День 30 остаётся как есть.
    """
    assert extract_date("[30.09.2026", today=TODAY) == date(2026, 9, 30)


def test_detect_handles_ocr_bracket_before_date():
    """Реальный кейс: «РАСПИСАНИЕ ЗВОНОКОВ\\n[7.10.2026»."""
    r = detect_bell_schedule(
        text="",  # только картинка, без текста от пользователя
        ocr_text="РАСПИСАНИЕ ЗВОНОКОВ\n[7.10.2026",
        today=date(2026, 10, 15),
    )
    assert r.is_bell_schedule is True
    assert r.target_date == date(2026, 10, 17)
