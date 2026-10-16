"""Тесты для ocr.py.

Tesseract не запускается по-настоящему — все вызовы к pytesseract мокаются.
Это нужно, чтобы тесты работали в CI и на машинах без установленного Tesseract.
"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

import ocr

# ---------- Вспомогательные функции ----------


def _make_png_bytes(width: int = 100, height: int = 50, color=(255, 255, 255)) -> bytes:
    """Генерирует валидный PNG-байт-код заданного размера."""
    img = Image.new("RGB", (width, height), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def reset_ocr_state():
    """Сбрасывает кэш is_available между тестами."""
    ocr._availability_checked = False
    ocr._available = False
    yield
    ocr._availability_checked = False
    ocr._available = False


# ---------- is_available ----------


def test_is_available_true_when_tesseract_works(monkeypatch):
    """Если pytesseract импортируется и get_tesseract_version работает — True."""
    fake_pytesseract = MagicMock()
    fake_pytesseract.get_tesseract_version.return_value = "5.3.0"

    monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake_pytesseract)
    monkeypatch.setattr(ocr, "_configure_tesseract", lambda: None)
    # Обходим проверку через shutil.which
    monkeypatch.setattr(ocr.shutil, "which", lambda name: "/usr/bin/tesseract")

    assert ocr.is_available() is True


def test_is_available_false_when_version_raises(monkeypatch):
    """Если get_tesseract_version кидает — False."""
    fake_pytesseract = MagicMock()
    fake_pytesseract.get_tesseract_version.side_effect = Exception("not found")

    monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake_pytesseract)
    monkeypatch.setattr(ocr, "_configure_tesseract", lambda: None)

    assert ocr.is_available() is False


def test_is_available_caches_result(monkeypatch):
    """Повторный вызов не пересчитывает (кэш работает)."""
    fake_pytesseract = MagicMock()
    fake_pytesseract.get_tesseract_version.return_value = "5.3.0"

    monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake_pytesseract)
    monkeypatch.setattr(ocr, "_configure_tesseract", lambda: None)
    monkeypatch.setattr(ocr.shutil, "which", lambda name: "/usr/bin/tesseract")

    assert ocr.is_available() is True
    # Меняем версию на «падающую» — кэш не должен пересчитываться
    fake_pytesseract.get_tesseract_version.side_effect = Exception("boom")
    assert ocr.is_available() is True


# ---------- extract_text: граничные случаи ----------


def test_extract_text_empty_bytes():
    """Пустые байты — пустая строка, без вызова Tesseract."""
    assert ocr.extract_text(b"") == ""


def test_extract_text_returns_empty_when_not_available(monkeypatch):
    """Если Tesseract недоступен — пустая строка."""
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    assert ocr.extract_text(_make_png_bytes()) == ""


def test_extract_text_invalid_image(monkeypatch):
    """Битые байты — пустая строка, без исключения."""
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    fake_pytesseract = MagicMock()
    monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake_pytesseract)

    result = ocr.extract_text(b"\x00\x01garbage")
    assert result == ""
    fake_pytesseract.image_to_string.assert_not_called()


# ---------- extract_text: с моком Tesseract ----------


def test_extract_text_success(monkeypatch):
    """Нормальный сценарий: картинка → текст."""
    monkeypatch.setattr(ocr, "is_available", lambda: True)

    fake_pytesseract = MagicMock()
    fake_pytesseract.image_to_string.return_value = "РАСПИСАНИЕ ЗВОНОКОВ\n05.10.2026"
    monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake_pytesseract)

    result = ocr.extract_text(_make_png_bytes())
    assert "РАСПИСАНИЕ ЗВОНОКОВ" in result
    assert "05.10.2026" in result
    # Проверяем, что вызвано несколько PSM (по умолчанию 3, 6, 11)
    assert fake_pytesseract.image_to_string.call_count == 3


def test_extract_text_combines_multiple_psms(monkeypatch):
    """Результаты разных PSM объединяются (важно для bell schedules)."""
    monkeypatch.setattr(ocr, "is_available", lambda: True)

    fake_pytesseract = MagicMock()

    # PSM 3 вернёт только дату, PSM 6 — заголовок, PSM 11 — что-то ещё
    def fake_ots(img, lang=None, config=""):
        if "psm 3" in config:
            return "05.10.2026"
        if "psm 6" in config:
            return "РАСПИСАНИЕ ЗВОНОКОВ"
        if "psm 11" in config:
            return "Обед для групп"
        return ""

    fake_pytesseract.image_to_string.side_effect = fake_ots
    monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake_pytesseract)

    result = ocr.extract_text(_make_png_bytes())
    assert "05.10.2026" in result
    assert "РАСПИСАНИЕ ЗВОНОКОВ" in result
    assert "Обед для групп" in result


def test_extract_text_empty_result(monkeypatch):
    """Tesseract вернул пустоту — возвращаем пустую строку."""
    monkeypatch.setattr(ocr, "is_available", lambda: True)

    fake_pytesseract = MagicMock()
    fake_pytesseract.image_to_string.return_value = ""
    monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake_pytesseract)

    assert ocr.extract_text(_make_png_bytes()) == ""


def test_extract_text_whitespace_only(monkeypatch):
    """Tesseract вернул только пробелы — тоже пусто."""
    monkeypatch.setattr(ocr, "is_available", lambda: True)

    fake_pytesseract = MagicMock()
    fake_pytesseract.image_to_string.return_value = "   \n\n  \t "
    monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake_pytesseract)

    assert ocr.extract_text(_make_png_bytes()) == ""


def test_extract_text_psm_error_does_not_crash(monkeypatch):
    """Один PSM упал — остальные всё равно дают результат."""
    monkeypatch.setattr(ocr, "is_available", lambda: True)

    fake_pytesseract = MagicMock()

    def fake_ots(img, lang=None, config=""):
        if "psm 6" in config:
            raise RuntimeError("mock error in PSM 6")
        return "РАСПИСАНИЕ ЗВОНОКОВ"

    fake_pytesseract.image_to_string.side_effect = fake_ots
    monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake_pytesseract)

    result = ocr.extract_text(_make_png_bytes())
    assert "РАСПИСАНИЕ ЗВОНОКОВ" in result


def test_extract_text_custom_psms(monkeypatch):
    """Можно передать свой набор PSM."""
    monkeypatch.setattr(ocr, "is_available", lambda: True)

    fake_pytesseract = MagicMock()
    fake_pytesseract.image_to_string.return_value = "текст"
    monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake_pytesseract)

    ocr.extract_text(_make_png_bytes(), psms=(6,))
    assert fake_pytesseract.image_to_string.call_count == 1


# ---------- _preprocess ----------


def test_preprocess_returns_image():
    """Препроцессинг не падает на валидной картинке."""
    img = Image.new("RGB", (100, 50), (128, 128, 128))
    result = ocr._preprocess(img)
    assert isinstance(result, Image.Image)
    # Увеличивается в 2 раза
    assert result.size == (200, 100)


def test_preprocess_rgba_to_rgb():
    """RGBA превращается в RGB без падения."""
    img = Image.new("RGBA", (100, 50), (255, 0, 0, 128))
    result = ocr._preprocess(img)
    assert result.mode == "L"  # на выходе grayscale
    assert result.size == (200, 100)
