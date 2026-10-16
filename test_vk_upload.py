"""Тесты для vk_upload.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import vk_upload
from vk_upload import _build_multipart, upload_png_to_vk

# ---------- _build_multipart ----------


def test_build_multipart_contains_boundary_and_filename():
    png = b"\x89PNG\r\n\x1a\nFAKE"
    body, content_type = _build_multipart(png)
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="photo"' in body
    assert b'filename="schedule.png"' in body
    assert b"Content-Type: image/png" in body
    assert png in body
    # Закрывающий boundary должен быть
    boundary = content_type.split("boundary=")[1]
    assert body.endswith(f"--{boundary}--\r\n".encode("utf-8"))


def test_build_multipart_custom_field():
    body, _ = _build_multipart(b"x", field_name="file", filename="a.png")
    assert b'name="file"' in body
    assert b'filename="a.png"' in body


# ---------- upload_png_to_vk ----------


@pytest.mark.asyncio
async def test_upload_png_success(monkeypatch):
    monkeypatch.setattr(vk_upload, "VK_GROUP_ID", None)

    api = MagicMock()
    api.photos.get_messages_upload_server = AsyncMock(return_value={"upload_url": "https://upload.example/"})
    api.photos.save_messages_photo = AsyncMock(return_value={"owner_id": -123, "id": 456})

    fake_response = MagicMock()
    fake_response.json.return_value = {"server": 1, "photo": '[{"id":1}]', "hash": "abc"}
    fake_response.raise_for_status = MagicMock()

    with patch("vk_upload.requests.post", return_value=fake_response) as post:
        attachment = await upload_png_to_vk(api, b"\x89PNG...", max_attempts=1)

    assert attachment == "photo-123_456"
    # Проверяем, что post вызван с data=bytes и headers
    _, kwargs = post.call_args
    assert "data" in kwargs
    assert isinstance(kwargs["data"], bytes)
    assert "Content-Type" in kwargs["headers"]


@pytest.mark.asyncio
async def test_upload_png_no_upload_url(monkeypatch):
    monkeypatch.setattr(vk_upload, "VK_GROUP_ID", None)
    api = MagicMock()
    api.photos.get_messages_upload_server = AsyncMock(return_value={})
    with pytest.raises(RuntimeError, match="upload_url"):
        await upload_png_to_vk(api, b"x", max_attempts=1)


@pytest.mark.asyncio
async def test_upload_png_list_response(monkeypatch):
    monkeypatch.setattr(vk_upload, "VK_GROUP_ID", None)
    api = MagicMock()
    api.photos.get_messages_upload_server = AsyncMock(return_value={"upload_url": "https://upload.example/"})
    api.photos.save_messages_photo = AsyncMock(return_value=[{"owner_id": -1, "id": 2}])
    fake_response = MagicMock()
    fake_response.json.return_value = {"server": 1, "photo": '[{"id":1}]', "hash": "h"}
    fake_response.raise_for_status = MagicMock()

    with patch("vk_upload.requests.post", return_value=fake_response):
        attachment = await upload_png_to_vk(api, b"x", max_attempts=1)

    assert attachment == "photo-1_2"


@pytest.mark.asyncio
async def test_upload_png_passes_peer_id(monkeypatch):
    monkeypatch.setattr(vk_upload, "VK_GROUP_ID", None)
    api = MagicMock()
    api.photos.get_messages_upload_server = AsyncMock(return_value={"upload_url": "https://upload.example/"})
    api.photos.save_messages_photo = AsyncMock(return_value={"owner_id": -1, "id": 2})
    fake_response = MagicMock()
    fake_response.json.return_value = {"server": 1, "photo": '[{"id":1}]', "hash": "h"}
    fake_response.raise_for_status = MagicMock()

    with patch("vk_upload.requests.post", return_value=fake_response):
        await upload_png_to_vk(api, b"x", peer_id=2000000001, max_attempts=1)

    api.photos.get_messages_upload_server.assert_awaited_once_with(peer_id=2000000001)


@pytest.mark.asyncio
async def test_upload_png_empty_photo_raises(monkeypatch):
    monkeypatch.setattr(vk_upload, "VK_GROUP_ID", None)
    api = MagicMock()
    api.photos.get_messages_upload_server = AsyncMock(return_value={"upload_url": "https://upload.example/"})
    api.photos.save_messages_photo = AsyncMock()
    fake_response = MagicMock()
    fake_response.json.return_value = {"server": 1, "photo": "[]", "hash": "h"}
    fake_response.raise_for_status = MagicMock()

    with patch("vk_upload.requests.post", return_value=fake_response):
        with pytest.raises(RuntimeError, match="VK не принял файл"):
            await upload_png_to_vk(api, b"x", peer_id=2000000001, max_attempts=1)

    api.photos.save_messages_photo.assert_not_called()


@pytest.mark.asyncio
async def test_upload_png_passes_group_id(monkeypatch):
    monkeypatch.setattr(vk_upload, "VK_GROUP_ID", 241466586)
    api = MagicMock()
    api.photos.get_messages_upload_server = AsyncMock(return_value={"upload_url": "https://upload.example/"})
    api.photos.save_messages_photo = AsyncMock(return_value={"owner_id": -1, "id": 2})
    fake_response = MagicMock()
    fake_response.json.return_value = {"server": 1, "photo": '[{"id":1}]', "hash": "h"}
    fake_response.raise_for_status = MagicMock()

    with patch("vk_upload.requests.post", return_value=fake_response):
        await upload_png_to_vk(api, b"x", peer_id=2000000001, max_attempts=1)

    api.photos.get_messages_upload_server.assert_awaited_once_with(peer_id=2000000001, group_id=241466586)
    api.photos.save_messages_photo.assert_awaited_once_with(photo='[{"id":1}]', server=1, hash="h", group_id=241466586)


@pytest.mark.asyncio
async def test_upload_png_retries_on_empty_photo(monkeypatch):
    """photo='' на первой попытке, валидно на второй → успех."""
    monkeypatch.setattr(vk_upload, "VK_GROUP_ID", None)

    api = MagicMock()
    api.photos.get_messages_upload_server = AsyncMock(return_value={"upload_url": "https://upload.example/"})
    api.photos.save_messages_photo = AsyncMock(return_value={"owner_id": -1, "id": 2})

    empty = MagicMock()
    empty.json.return_value = {"server": 1, "photo": "[]", "hash": "h"}
    empty.raise_for_status = MagicMock()

    ok = MagicMock()
    ok.json.return_value = {"server": 1, "photo": '[{"id":1}]', "hash": "h"}
    ok.raise_for_status = MagicMock()

    with patch("vk_upload.requests.post", side_effect=[empty, ok]) as post, patch(
        "vk_upload.asyncio.sleep", new=AsyncMock()
    ):
        attachment = await upload_png_to_vk(api, b"x", max_attempts=3)

    assert attachment == "photo-1_2"
    assert post.call_count == 2


# ============================================================
# download_photo
# ============================================================


@pytest.mark.asyncio
async def test_download_photo_success():
    from vk_upload import download_photo

    fake_response = MagicMock()
    fake_response.content = b"\x89PNG...data"
    fake_response.raise_for_status = MagicMock()

    with patch("vk_upload.requests.get", return_value=fake_response) as get:
        result = await download_photo("https://example.com/photo.jpg")

    assert result == b"\x89PNG...data"
    get.assert_called_once()


@pytest.mark.asyncio
async def test_download_photo_returns_none_on_error():
    from vk_upload import download_photo

    with patch("vk_upload.requests.get", side_effect=Exception("network down")):
        result = await download_photo("https://example.com/photo.jpg")

    assert result is None


@pytest.mark.asyncio
async def test_download_photo_empty_url():
    from vk_upload import download_photo

    result = await download_photo("")
    assert result is None


# ============================================================
# pick_best_photo_size
# ============================================================


def test_pick_best_photo_size():
    from vk_upload import pick_best_photo_size

    sizes = [
        {"type": "s", "url": "https://s.jpg", "width": 75, "height": 75},
        {"type": "m", "url": "https://m.jpg", "width": 130, "height": 130},
        {"type": "x", "url": "https://x.jpg", "width": 600, "height": 400},
        {"type": "o", "url": "https://o.jpg", "width": 1280, "height": 853},
    ]
    assert pick_best_photo_size(sizes) == "https://o.jpg"


def test_pick_best_photo_size_empty():
    from vk_upload import pick_best_photo_size

    assert pick_best_photo_size([]) is None
    assert pick_best_photo_size(None) is None


def test_pick_best_photo_size_single():
    from vk_upload import pick_best_photo_size

    sizes = [{"type": "x", "url": "https://only.jpg", "width": 600, "height": 400}]
    assert pick_best_photo_size(sizes) == "https://only.jpg"


@pytest.mark.asyncio
async def test_upload_png_retries_on_5xx(monkeypatch):
    """504 на первой попытке, успех на второй."""
    monkeypatch.setattr(vk_upload, "VK_GROUP_ID", None)

    api = MagicMock()
    api.photos.get_messages_upload_server = AsyncMock(return_value={"upload_url": "https://upload.example/"})
    api.photos.save_messages_photo = AsyncMock(return_value={"owner_id": -1, "id": 2})

    err = MagicMock()
    err.status_code = 504
    err.text = "Gateway Time-out"

    ok = MagicMock()
    ok.status_code = 200
    ok.json.return_value = {"server": 1, "photo": '[{"id":1}]', "hash": "h"}
    ok.raise_for_status = MagicMock()

    with patch("vk_upload.requests.post", side_effect=[err, ok]) as post, patch(
        "vk_upload.asyncio.sleep", new=AsyncMock()
    ):
        attachment = await upload_png_to_vk(api, b"x", max_attempts=3)

    assert attachment == "photo-1_2"
    assert post.call_count == 2


@pytest.mark.asyncio
async def test_upload_png_5xx_all_attempts_fail(monkeypatch):
    """Все попытки вернули 504 — RuntimeError наружу, не HTTPError."""
    monkeypatch.setattr(vk_upload, "VK_GROUP_ID", None)

    api = MagicMock()
    api.photos.get_messages_upload_server = AsyncMock(return_value={"upload_url": "https://upload.example/"})

    err = MagicMock()
    err.status_code = 504
    err.text = "Gateway Time-out"

    with patch("vk_upload.requests.post", return_value=err), patch("vk_upload.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(vk_upload.VKTemporaryError):
            await upload_png_to_vk(api, b"x", max_attempts=2)


# ============================================================
# content_type и filename
# ============================================================


def test_build_multipart_custom_content_type():
    """_build_multipart подставляет переданный MIME-тип."""
    body, _ = _build_multipart(b"JPEGDATA", content_type="image/jpeg", filename="a.jpg")
    assert b"Content-Type: image/jpeg" in body
    assert b'filename="a.jpg"' in body


def test_build_multipart_default_png():
    body, _ = _build_multipart(b"X")
    assert b"Content-Type: image/png" in body
    assert b'filename="schedule.png"' in body


@pytest.mark.asyncio
async def test_upload_png_passes_content_type(monkeypatch):
    """content_type и filename доходят до requests.post."""
    monkeypatch.setattr(vk_upload, "VK_GROUP_ID", None)

    api = MagicMock()
    api.photos.get_messages_upload_server = AsyncMock(return_value={"upload_url": "https://upload.example/"})
    api.photos.save_messages_photo = AsyncMock(return_value={"owner_id": -1, "id": 2})
    fake_response = MagicMock()
    fake_response.json.return_value = {"server": 1, "photo": '[{"id":1}]', "hash": "h"}
    fake_response.raise_for_status = MagicMock()

    with patch("vk_upload.requests.post", return_value=fake_response) as post:
        await upload_png_to_vk(api, b"\xff\xd8\xffJPEG", content_type="image/jpeg", filename="schedule.jpg")

    _, kwargs = post.call_args
    assert b"Content-Type: image/jpeg" in kwargs["data"]
    assert b'filename="schedule.jpg"' in kwargs["data"]
