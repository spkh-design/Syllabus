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
        attachment = await upload_png_to_vk(api, b"\x89PNG...")

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
        await upload_png_to_vk(api, b"x")


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
        attachment = await upload_png_to_vk(api, b"x")

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
        await upload_png_to_vk(api, b"x", peer_id=2000000001)

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
            await upload_png_to_vk(api, b"x", peer_id=2000000001)

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
        await upload_png_to_vk(api, b"x", peer_id=2000000001)

    api.photos.get_messages_upload_server.assert_awaited_once_with(peer_id=2000000001, group_id=241466586)
    api.photos.save_messages_photo.assert_awaited_once_with(photo='[{"id":1}]', server=1, hash="h", group_id=241466586)
