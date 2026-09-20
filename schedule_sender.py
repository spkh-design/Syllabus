"""Общий хелпер отправки ScheduleMessage в VK.

Знает про рендер (renderer), загрузку (vk_upload) и БД (week_images).
Используется и в bot.py (background_checker), и в handlers.py.
"""

from __future__ import annotations

import asyncio
import logging

import database as db
from renderer import render_day, render_week
from vk_upload import upload_png_to_vk

logger = logging.getLogger(__name__)


async def send_schedule_message(bot_api, peer_id: int, msg, *, conn=None, group_uuid: str | None = None) -> None:
    """Отправляет ScheduleMessage в VK.

    Логика:
      1. Если msg.png_bytes уже есть — используем его.
      2. Если kind='day_image' — рендерим (без сохранения в БД).
      3. Если kind='week_image' — рендерим и сохраняем в БД (UPSERT).
      4. Загружаем PNG в VK → attachment.
      5. Отправляем message + attachment.
      6. При любой ошибке — fallback на текст.
    """
    png_bytes = msg.png_bytes

    if png_bytes is None:
        try:
            if msg.kind == "day_image" and msg.target_date and msg.lessons is not None:
                png_bytes = await asyncio.to_thread(
                    render_day, msg.target_date, msg.lessons, msg.home_territory or "", msg.changes
                )
            elif msg.kind == "week_image" and msg.week_start and msg.week is not None:
                png_bytes = await asyncio.to_thread(
                    render_week, msg.week_start, msg.week, msg.home_territory or "", msg.changes_by_day
                )
                if conn is not None and group_uuid is not None:
                    db.save_week_image(conn, group_uuid, msg.week_start, png_bytes)
        except Exception as e:
            logger.exception("Ошибка рендера PNG: %s", e)
            png_bytes = None

    if png_bytes is None:
        # Нечего отправлять картинкой — отправляем текст (fallback)
        text = msg.text or msg.fallback_text or "⚠️ Не удалось сформировать расписание."
        await bot_api.messages.send(peer_id=peer_id, message=text, random_id=0)
        return

    try:
        attachment = await upload_png_to_vk(bot_api, png_bytes, peer_id=peer_id)
        await bot_api.messages.send(peer_id=peer_id, message=msg.text or "", attachment=attachment, random_id=0)
    except Exception as e:
        logger.exception("Не удалось загрузить/отправить изображение: %s", e)
        fallback = msg.fallback_text or msg.text or "⚠️ Не удалось отправить расписание."
        await bot_api.messages.send(peer_id=peer_id, message=fallback, random_id=0)
