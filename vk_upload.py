"""Загрузка PNG в VK. Возвращает attachment-строку для messages.send.

Публичный API:
    upload_png_to_vk(bot_api, png_bytes, *, peer_id=None, group_id=None) -> str
"""

from __future__ import annotations

import asyncio
import logging
import secrets

import requests

from config import VK_GROUP_ID

logger = logging.getLogger(__name__)


class VKTemporaryError(Exception):
    """Временная ошибка VK upload-сервера (5xx, 429). Retry-ибельна."""


UPLOAD_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _as_dict(obj) -> dict:
    """vkbottle может вернуть dict или pydantic-модель."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return {k: getattr(obj, k) for k in ("upload_url", "server", "photo", "hash", "id", "owner_id") if hasattr(obj, k)}


def _build_multipart(
    image_bytes: bytes, field_name: str = "photo", filename: str = "schedule.png", content_type: str = "image/png"
) -> tuple[bytes, str]:
    """Ручная сборка multipart/form-data.

    Args:
        image_bytes: содержимое картинки.
        field_name: имя поля в multipart (обычно «photo»).
        filename: имя файла в заголовке Content-Disposition.
        content_type: MIME-тип файла («image/png», «image/jpeg»).

    Returns:
        (body, content_type_header) — тело запроса и значение
        заголовка Content-Type с boundary.
    """
    boundary = "----WebKitFormBoundary" + secrets.token_hex(16)
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; '
        f'filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + image_bytes + tail
    return body, f"multipart/form-data; boundary={boundary}"


async def upload_png_to_vk(
    bot_api,
    png_bytes: bytes,
    *,
    peer_id: int | None = None,
    group_id: int | None = None,
    max_attempts: int = 4,
    retry_delay: float = 3.0,
    content_type: str = "image/png",
    filename: str = "schedule.png",
) -> str:
    """Загружает картинку в VK и возвращает attachment.

    При photo='' / photo='[]' / 5xx / 405 — повторяет попытку через
    retry_delay * attempt секунд, до max_attempts раз.

    Args:
        bot_api: vkbottle API.
        png_bytes: содержимое картинки (PNG, JPEG или другой формат).
        peer_id: ID чата, куда будет отправлено фото.
        group_id: ID сообщества. По умолчанию — из config.
        max_attempts: сколько раз пытаться при временных ошибках.
        retry_delay: базовый интервал между попытками, секунды.
        content_type: MIME-тип картинки («image/png» или «image/jpeg»).
        filename: имя файла в multipart (для Content-Disposition).

    Returns:
        attachment — строка вида "photo-123456_789".

    Raises:
        RuntimeError / VKTemporaryError — если VK отказался
        принять файл после всех попыток.
    """
    if group_id is None:
        group_id = VK_GROUP_ID

    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await _upload_png_once(
                bot_api, png_bytes, peer_id=peer_id, group_id=group_id, content_type=content_type, filename=filename
            )
        except VKTemporaryError as e:
            last_error = e
            if attempt < max_attempts:
                # Backoff: 3с, 6с, 9с...
                wait = retry_delay * attempt
                logger.warning(
                    "VK временно недоступен (попытка %d/%d), повтор через %.1f с: %s", attempt, max_attempts, wait, e
                )
                await asyncio.sleep(wait)
                continue
        except RuntimeError as e:
            last_error = e
            # «VK не принял файл» — тоже retry-ибельно (photo='' / photo='[]').
            if "VK не принял файл" in str(e):
                if attempt < max_attempts:
                    wait = retry_delay * attempt
                    logger.warning(
                        "VK отверг PNG (попытка %d/%d), повтор через %.1f с: %s", attempt, max_attempts, wait, e
                    )
                    await asyncio.sleep(wait)
                    continue
            raise

    raise last_error or RuntimeError("upload_png_to_vk: не удалось загрузить")


async def _upload_png_once(
    bot_api,
    png_bytes: bytes,
    *,
    peer_id: int | None,
    group_id: int | None,
    content_type: str = "image/png",
    filename: str = "schedule.png",
) -> str:
    """Одна попытка загрузки PNG в VK."""
    params: dict = {}
    if peer_id is not None:
        params["peer_id"] = peer_id
    if group_id is not None:
        params["group_id"] = group_id

    server_resp = await bot_api.photos.get_messages_upload_server(**params)
    server_dict = _as_dict(server_resp)
    upload_url = server_dict.get("upload_url")
    if not upload_url:
        raise RuntimeError(f"VK не вернул upload_url: {server_dict}")

    logger.info("VK upload: peer=%r, png=%d bytes (%.1f MB)", peer_id, len(png_bytes), len(png_bytes) / 1024 / 1024)
    body, content_type_header = _build_multipart(png_bytes, filename=filename, content_type=content_type)
    headers = {"Content-Type": content_type_header, "User-Agent": USER_AGENT}

    response = await asyncio.to_thread(requests.post, upload_url, data=body, headers=headers, timeout=UPLOAD_TIMEOUT)
    if response.status_code in (403, 405, 429, 500, 502, 503, 504):
        raise VKTemporaryError(f"VK upload-server HTTP {response.status_code}: {response.text[:200]}")
    response.raise_for_status()
    uploaded = response.json()

    logger.debug(
        "VK upload-server response keys=%s, png_size=%d, peer_id=%r, group_id=%r",
        list(uploaded.keys()),
        len(png_bytes),
        peer_id,
        group_id,
    )

    photo_field = uploaded.get("photo")
    if not photo_field or photo_field == "[]":
        raise RuntimeError(
            f"VK не принял файл, photo={photo_field!r}. "
            f"peer_id={peer_id}, group_id={group_id}, "
            f"png size={len(png_bytes)} bytes"
        )

    save_params: dict = {"photo": photo_field, "server": uploaded["server"], "hash": uploaded["hash"]}
    if group_id is not None:
        save_params["group_id"] = group_id

    saved = await bot_api.photos.save_messages_photo(**save_params)

    if isinstance(saved, list):
        if not saved:
            raise RuntimeError("save_messages_photo вернул пустой список")
        photo = saved[0]
    else:
        photo = saved

    photo_dict = _as_dict(photo)
    owner_id = photo_dict.get("owner_id")
    photo_id = photo_dict.get("id")
    if owner_id is None or photo_id is None:
        raise RuntimeError(f"Не удалось извлечь owner_id/id: {photo_dict}")

    attachment = f"photo{owner_id}_{photo_id}"
    logger.info("PNG загружен в VK: %s", attachment)
    return attachment


# ---------- Скачивание картинки по URL ----------

DOWNLOAD_TIMEOUT = 30


async def download_photo(url: str) -> bytes | None:
    """Скачивает картинку по URL из VK (или любой другой http(s) URL).

    Returns:
        Байты картинки или None при ошибке.
    """
    if not url:
        return None
    try:
        response = await asyncio.to_thread(requests.get, url, timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        return response.content
    except Exception as e:
        logger.warning("Не удалось скачать картинку %s: %s", url[:80], e)
        return None


def pick_best_photo_size(sizes: list[dict]) -> str | None:
    """Выбирает URL самого большого размера из списка sizes VK.

    VK возвращает sizes как список {"type": "s"|"m"|"x"|"o"|..., "url": ..., "width": ..., "height": ...}.
    Берём максимальный по width * height. Для OCR чем больше — тем лучше.
    """
    if not sizes:
        return None
    best = max(sizes, key=lambda s: (s.get("width") or 0) * (s.get("height") or 0))
    return best.get("url")
