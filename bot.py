"""Точка входа бота.

Запускает:
  1) long-polling ВК (обработка команд от пользователей);
  2) фоновый цикл проверки расписания (scheduler).

Оба работают в одном asyncio-loop.
"""

from __future__ import annotations

import asyncio
import logging

from vkbottle.bot import Bot, Message

from ocr import extract_text as ocr_extract_text
from ocr import is_available as ocr_is_available

from bell_sender import run_bell_checks

from datetime import date as _date

import database as db
import scheduler
from schedule_sender import send_schedule_message
from config import VK_TOKEN, CHECK_INTERVAL_MINUTES, DB_PATH, ADMIN_PEER_ID
from bell_detector import detect_bell_schedule
from vk_upload import download_photo, pick_best_photo_size
from handlers import (
    cmd_add_group,
    cmd_bell,
    cmd_curator,
    cmd_group,
    cmd_groups,
    cmd_interval,
    cmd_metrics,
    cmd_nextweek,
    cmd_remove_group,
    cmd_set_curator,
    cmd_start,
    cmd_status,
    cmd_subscribers,
    cmd_test,
    cmd_today,
    cmd_tomorrow,
    cmd_unsubscribe,
    cmd_use,
    cmd_week,
    cmd_week_skip,
    cmd_where,
    cmd_whoami,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Глобальное соединение с БД (vkbottle + asyncio + sqlite)
conn = db.init_db(DB_PATH)

# Глобальный кэш справочников (в памяти, чтобы не читать из БД каждый раз)
_base_cache: dict | None = None


bot = Bot(token=VK_TOKEN)


# ---------- Сохраняем все входящие сообщения ----------


@bot.on.raw_event("message_new")
async def save_incoming_raw(event: dict) -> None:
    """Ловит ВСЕ message_new события Long Poll, до всех фильтров.

    Сохраняет в chat_messages, при наличии фото — прогоняет OCR,
    детектит расписание звонков (по тексту + OCR), сохраняет в bell_schedules.
    """
    try:
        obj = event.get("object", {}) if isinstance(event, dict) else {}
        msg = obj.get("message", {}) if isinstance(obj, dict) else {}
        if not msg:
            return

        peer_id = msg.get("peer_id")
        from_id = msg.get("from_id")
        msg_id = msg.get("conversation_message_id") or msg.get("id") or 0
        text = msg.get("text", "") or ""

        # --- Вложения ---
        attachments = []
        photo_url: str | None = None
        for att in msg.get("attachments", []) or []:
            att_type = att.get("type")
            if att_type == "photo":
                photo = att.get("photo", {}) or {}
                sizes = photo.get("sizes", []) or []
                sizes_list = [
                    {"type": s.get("type"), "url": s.get("url"), "width": s.get("width"), "height": s.get("height")}
                    for s in sizes
                ]
                attachments.append(
                    {"type": "photo", "owner_id": photo.get("owner_id"), "id": photo.get("id"), "sizes": sizes_list}
                )
                if photo_url is None:
                    photo_url = pick_best_photo_size(sizes_list)
            else:
                attachments.append({"type": att_type})

        if not text and not attachments:
            return

        # --- Сохраняем сообщение ---
        db.save_chat_message(
            conn, peer_id=peer_id, message_id=msg_id, from_id=from_id, text=text, attachments=attachments
        )

        # --- OCR по фото (если оно есть и OCR доступен) ---
        image_bytes: bytes | None = None
        ocr_text = ""
        if photo_url:
            image_bytes = await download_photo(photo_url)
            if image_bytes and ocr_is_available():
                # Tesseract — синхронный, уводим в поток, чтобы не блокировать loop
                ocr_text = await asyncio.to_thread(ocr_extract_text, image_bytes)
                if ocr_text:
                    logger.info(
                        "OCR done: peer=%s msg=%s chars=%d preview=%r", peer_id, msg_id, len(ocr_text), ocr_text[:80]
                    )
                # Помечаем, что OCR для этого сообщения прогоняли
                db.mark_chat_message_ocr_done(conn, peer_id, msg_id)

        # --- Детект bell schedule (text + ocr) ---
        result = detect_bell_schedule(text, ocr_text=ocr_text)
        if not result.is_bell_schedule or result.target_date is None:
            return

        today = _date.today()
        if result.target_date < today:
            logger.warning(
                "Bell schedule определён на %s — это прошлое (сегодня %s), " "пропускаем. text=%r ocr_preview=%r",
                result.target_date,
                today,
                text[:60],
                ocr_text[:80],
            )
            return

        # Картинку сохраняем только для найденных bell schedules,
        # чтобы не раздувать БД байтами всех фото подряд.
        db.save_bell_schedule(
            conn,
            peer_id=peer_id,
            target_date=result.target_date,
            source_msg_id=msg_id,
            from_id=from_id,
            image_bytes=image_bytes,
            caption=text,
            ocr_text=ocr_text,
        )
        logger.info(
            "Bell schedule detected: peer=%s target=%s from_msg=%s img=%s ocr_chars=%d",
            peer_id,
            result.target_date,
            msg_id,
            f"{len(image_bytes)}B" if image_bytes else "none",
            len(ocr_text),
        )
    except Exception as e:
        logger.exception("Ошибка обработки raw_event: %s", e)


# ---------- Обработчики команд ----------


@bot.on.message(text=["/start", "/help", "начать", "start"])
async def on_start(message: Message):
    await cmd_start(message, conn)


@bot.on.message(text="/group <args>")
async def on_group(message: Message, args: str):
    await cmd_group(message, conn, args)


@bot.on.message(text="/where")
async def on_where(message: Message):
    await cmd_where(message, conn)


@bot.on.message(text="/today")
async def on_today(message: Message):
    await cmd_today(message, conn)


@bot.on.message(text="/tomorrow")
async def on_tomorrow(message: Message):
    await cmd_tomorrow(message, conn)


@bot.on.message(text="/week")
async def on_week(message: Message):
    await cmd_week(message, conn)


@bot.on.message(text="/nextweek")
async def on_nextweek(message: Message):
    await cmd_nextweek(message, conn)


@bot.on.message(text="/status")
async def on_status(message: Message):
    await cmd_status(message, conn)


@bot.on.message(text="/unsubscribe")
async def on_unsubscribe(message: Message):
    await cmd_unsubscribe(message, conn)


@bot.on.message(text="/whoami")
async def on_whoami(message: Message):
    await cmd_whoami(message, conn)


@bot.on.message(text="/interval <args>")
async def on_interval(message: Message, args: str):
    await cmd_interval(message, conn, args)


@bot.on.message(text="/test")
async def on_test(message: Message):
    await cmd_test(message, conn)


@bot.on.message(text="/metrics")
async def on_metrics(message: Message):
    await cmd_metrics(message, conn)


@bot.on.message(text="/subscribers")
async def on_subscribers(message: Message):
    await cmd_subscribers(message, conn)


@bot.on.message(text="/week_skip")
async def on_week_skip(message: Message):
    await cmd_week_skip(message, conn)


@bot.on.message(text="/week_skip <args>")
async def on_week_skip_arg(message: Message, args: str):
    await cmd_week_skip(message, conn, args)


# ---------- Multi-tenant: управление группами ----------


@bot.on.message(text=["/add_group", "/add_group <args>"])
async def on_add_group(message: Message, args: str = ""):
    await cmd_add_group(message, conn, args)


@bot.on.message(text=["/remove_group", "/remove_group <args>"])
async def on_remove_group(message: Message, args: str = ""):
    await cmd_remove_group(message, conn, args)


@bot.on.message(text=["/use", "/use <args>"])
async def on_use(message: Message, args: str = ""):
    await cmd_use(message, conn, args)


@bot.on.message(text="/groups")
async def on_groups(message: Message):
    await cmd_groups(message, conn)


# ---------- Multi-tenant: куратор ----------


@bot.on.message(text=["/set_curator", "/set_curator <args>"])
async def on_set_curator(message: Message, args: str = ""):
    await cmd_set_curator(message, conn, args)


@bot.on.message(text="/curator")
async def on_curator(message: Message):
    await cmd_curator(message, conn)


@bot.on.message(text=["/bell", "/bell <args>"])
async def on_bell(message: Message, args: str = ""):
    await cmd_bell(message, conn, args)


@bot.on.message()
async def on_unknown(message: Message):
    """Реагирует только на команды (текст, начинающийся с /).

    На всё остальное — молчит.
    """
    text = (message.text or "").strip()
    if not text.startswith("/"):
        return
    await message.answer("🤔 Не понимаю команду.\n" "Напишите /help, чтобы увидеть список.")


# ---------- Фоновый цикл проверки ----------


async def send_startup_greeting(conn, admin_peer_id: int | None = ADMIN_PEER_ID, send_func=None) -> None:
    """Отправляет приветствие при первом запуске (БД пустая).

    Args:
        conn: соединение с БД.
        admin_peer_id: ID получателя. По умолчанию — из config.
        send_func: функция отправки. Для тестов передают mock.
                   По умолчанию — реальный VK API.
    """
    if admin_peer_id is None:
        logger.info("ADMIN_PEER_ID не задан — приветствие не отправляется")
        return

    groups = db.get_all_groups(conn, only_active=False)
    if groups:
        logger.info("БД уже настроена (%d групп) — приветствие не отправляется", len(groups))
        return

    text = (
        "👋 Бот запустился.\n\n"
        "Группа ещё не настроена. Если у вас есть права — "
        "настройте командой:\n"
        "  /group СП-N №группы\n\n"
        "Где СП-N — подразделение, №группы — номер группы."
    )

    if send_func is None:
        send_func = bot.api.messages.send

    try:
        await send_func(peer_id=admin_peer_id, message=text, random_id=0)
        logger.info("Приветствие отправлено в peer_id=%s", admin_peer_id)
    except Exception as e:
        logger.error("Не удалось отправить приветствие: %s", e)


async def background_checker():
    """Раз в CHECK_INTERVAL_MINUTES проверяет расписание всех активных групп."""
    global _base_cache
    logger.info(">>> background_checker: стартовал")

    while True:
        try:
            # 1. Ежедневная очистка устаревших данных
            if db.should_run_daily_cleanup(conn):
                _run_daily_cleanup(conn)
                db.mark_daily_cleanup_done(conn)

            # 2. Отправка bell schedules / пинг куратора (после 18:00, раз в сутки)
            try:
                await run_bell_checks(conn, bot.api)
            except Exception as e:
                logger.exception("Ошибка bell_checks: %s", e)

            # 3. Список активных групп
            groups = db.get_all_groups(conn, only_active=True)
            if not groups:
                logger.info(">>> checker: активных групп нет")
            else:
                # 3. Справочники общие для всех групп — обновляем один раз
                _base_cache = scheduler.refresh_base_info_if_needed(conn, _base_cache)
                if _base_cache is None:
                    logger.warning("Справочники недоступны — пропуск цикла")
                else:
                    for g in groups:
                        group_uuid = g["group_uuid"]
                        peer_id = g["peer_id"]
                        group_name = g["group_name"]
                        try:
                            messages = scheduler.run_check_cycle(conn, _base_cache, group_uuid)
                            logger.info(
                                ">>> checker: группа %s (peer=%s), %d сообщений", group_name, peer_id, len(messages)
                            )
                            for msg in messages:
                                try:
                                    await send_schedule_message(bot.api, peer_id, msg, conn=conn, group_uuid=group_uuid)
                                    await asyncio.sleep(2.0)
                                except Exception as e:
                                    logger.error("Ошибка отправки в peer=%s: %s", peer_id, e)
                        except Exception as e:
                            logger.exception("Ошибка цикла для группы %s: %s", group_name, e)
        except Exception as e:
            logger.exception("Ошибка в фоновом цикле: %s", e)

        interval = db.get_setting(conn, "check_interval_minutes", CHECK_INTERVAL_MINUTES)
        logger.info(">>> checker: спим %s мин", interval)
        await asyncio.sleep(int(interval) * 60)


def _run_daily_cleanup(conn) -> None:
    """Раз в сутки чистит устаревшие данные по всем таблицам."""
    n_snapshots = db.cleanup_old_snapshots(conn, keep_days=30)
    n_weeks = db.cleanup_old_announced_weeks(conn, keep_days=30)
    n_imgs = db.cleanup_old_week_images(conn, keep_days=30)
    n_chat = db.cleanup_old_chat_messages(conn, keep_days=30)
    n_bell = db.cleanup_old_bell_schedules(conn)
    n_pings = db.cleanup_old_holiday_pings(conn, keep_days=30)
    logger.info(
        "Ежедневная очистка: snapshots=%d, announced_weeks=%d, week_images=%d, "
        "chat_messages=%d, bell_schedules=%d, holiday_pings=%d",
        n_snapshots,
        n_weeks,
        n_imgs,
        n_chat,
        n_bell,
        n_pings,
    )


# ---------- Точка входа ----------


async def main():
    logger.info("Запуск бота...")

    # Однократная миграция settings → groups (если в БД старый формат)
    if db.migrate_settings_to_groups(conn):
        logger.info("Выполнена миграция настроек в groups")

    global _base_cache
    _base_cache = scheduler.refresh_base_info_if_needed(conn, None)
    if _base_cache is None:
        logger.warning("Не удалось загрузить справочники при старте")

    # Приветствие — до запуска polling, чтобы точно ушло
    await send_startup_greeting(conn)

    # Запускаем фоновый цикл
    asyncio.create_task(background_checker())

    # Запускаем long-polling ВК
    await bot.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
