"""OCR: извлечение текста из картинок через Tesseract.

Публичный API:
    extract_text(image_bytes, *, lang="rus+eng") -> str
    is_available() -> bool

Модуль спокойно переживает отсутствие Tesseract — в этом случае
возвращает пустую строку и пишет warning в лог. Это нужно, чтобы
тесты и локальная разработка без установленного Tesseract не падали.
"""

from __future__ import annotations

import io
import logging
import os
import shutil

from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

_OCR_PSMS = (3, 6, 11)

# Флаг «мы уже проверяли наличие Tesseract» — чтобы не искать каждый раз.
_availability_checked = False
_available = False


def _configure_tesseract() -> None:
    """Настраивает путь к tesseract.exe.

    На Windows pytesseract не всегда находит исполняемый файл сам —
    он ожидает его в PATH. Если в PATH нет, но файл лежит в стандартном
    месте установки (C:\\Program Files\\Tesseract-OCR), пропишем путь явно.
    """
    # Уже установлен через env var? Уважаем.
    if os.environ.get("TESSERACT_CMD"):
        try:
            import pytesseract

            pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]
            return
        except ImportError:
            return

    # Если в PATH — не трогаем
    tesseract_path = shutil.which("tesseract")
    if tesseract_path:
        logger.info("Tesseract найден в PATH: %s", tesseract_path)
        return

    # Windows fallback: стандартная папка установки
    for candidate in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if os.path.exists(candidate):
            try:
                import pytesseract

                pytesseract.pytesseract.tesseract_cmd = candidate
                logger.info("Найден tesseract: %s", candidate)
                return
            except ImportError:
                return


def is_available() -> bool:
    """True, если Tesseract установлен и доступен."""
    global _availability_checked, _available
    if _availability_checked:
        return _available

    _availability_checked = True
    try:
        import pytesseract
    except ImportError:
        logger.warning("pytesseract не установлен — OCR отключён")
        _available = False
        return False

    _configure_tesseract()

    try:
        # Проверяем не только наличие бинарника, но и версию.
        # get_tesseract_version кидает исключение, если файла нет.
        pytesseract.get_tesseract_version()
        _available = True
        logger.info("Tesseract доступен для OCR")
    except Exception as e:
        logger.warning("Tesseract недоступен (%s) — OCR отключён", e)
        _available = False

    return _available


def _preprocess(img: Image.Image) -> Image.Image:
    """Готовит картинку для OCR.

    Шаги:
      - градации серого,
      - увеличение в 2 раза (Tesseract лучше читает крупный текст),
      - лёгкое размытие для сглаживания шума,
      - автоконтраст.

    Формат остаётся PIL.Image, конвертация в bytes не нужна —
    pytesseract принимает PIL.Image напрямую.
    """
    # Приводим к RGB (на случай PNG с альфа-каналом)
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg

    img = img.convert("L")  # grayscale

    # Увеличиваем: Tesseract-у проще читать большой текст
    w, h = img.size
    img = img.resize((w * 2, h * 2), Image.LANCZOS)

    # Размытие убирает пиксельный шум от JPEG-артефактов
    img = img.filter(ImageFilter.MedianFilter(size=3))

    # Автоконтраст — растягивает диапазон яркостей
    img = ImageOps.autocontrast(img)

    return img


def extract_text(image_bytes: bytes, *, lang: str = "rus+eng", psms: tuple[int, ...] = _OCR_PSMS) -> str:
    """Извлекает текст из картинки через Tesseract.

    Прогоняет OCR несколькими PSM (page segmentation modes) и объединяет
    результаты. Разные режимы ошибаются по-разному, вместе они дают
    более полный текст. Это критично для bell schedules: заголовок
    «РАСПИСАНИЕ ЗВОНОКОВ» часто теряется в одном PSM, но находится в другом.

    Args:
        image_bytes: содержимое PNG/JPG.
        lang: языки для распознавания (через +), по умолчанию «rus+eng».
        psms: какие PSM использовать. По умолчанию — (3, 6, 11).

    Returns:
        Распознанный текст (объединённый из нескольких PSM).
        Пустая строка при отсутствии Tesseract или ошибке.
    """
    if not image_bytes:
        return ""

    if not is_available():
        return ""

    try:
        import pytesseract
    except ImportError:
        return ""

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except Exception as e:
        logger.warning("OCR: не удалось открыть картинку (%s)", e)
        return ""

    try:
        prepped = _preprocess(img)
    except Exception as e:
        logger.warning("OCR: ошибка препроцессинга (%s)", e)
        return ""

    parts: list[str] = []
    for psm in psms:
        try:
            t = pytesseract.image_to_string(prepped, lang=lang, config=f"--psm {psm}")
            if t and t.strip():
                parts.append(t.strip())
                logger.debug("OCR PSM=%d: %d символов", psm, len(t.strip()))
        except Exception as e:
            logger.warning("OCR: ошибка PSM=%d (%s)", psm, e)

    text = "\n\n".join(parts)
    if text:
        logger.debug("OCR: объединённый текст %d символов", len(text))
    return text
