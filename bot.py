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

import database as db
import scheduler
from config import VK_TOKEN, CHECK_INTERVAL_MINUTES, DB_PATH
from handlers import (
    cmd_group,
    cmd_interval,
    cmd_metrics,
    cmd_start,
    cmd_status,
    cmd_subscribers,
    cmd_test,
    cmd_today,
    cmd_tomorrow,
    cmd_unsubscribe,
    cmd_week,
    cmd_where,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Глобальное соединение с БД (vkbottle + asyncio + sqlite)
conn = db.init_db(DB_PATH)

# Глобальный кэш справочников (в памяти, чтобы не читать из БД каждый раз)
_base_cache: dict | None = None


bot = Bot(token=VK_TOKEN)


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


@bot.on.message(text="/status")
async def on_status(message: Message):
    await cmd_status(message, conn)


@bot.on.message(text="/unsubscribe")
async def on_unsubscribe(message: Message):
    await cmd_unsubscribe(message, conn)


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


@bot.on.message()
async def on_unknown(message: Message):
    await message.answer(
        "🤔 Не понимаю команду.\n"
        "Напишите /help, чтобы увидеть список."
    )

# ---------- Фоновый цикл проверки ----------

async def background_checker():
    """Раз в CHECK_INTERVAL_MINUTES проверяет расписание."""
    global _base_cache

    # Небольшая задержка при старте, чтобы бот успел подняться
    await asyncio.sleep(5)

    while True:
        try:
            group_uuid = db.get_setting(conn, "group_uuid")
            peer_id = db.get_setting(conn, "peer_id")

            if group_uuid and peer_id:
                # Обновим справочники, если нужно
                _base_cache = scheduler.refresh_base_info_if_needed(
                    conn, _base_cache,
                )
                if _base_cache is None:
                    logger.warning("Справочники недоступны — пропуск цикла")
                else:
                    messages = scheduler.run_check_cycle(
                        conn, _base_cache, str(group_uuid),
                    )
                    for text in messages:
                        try:
                            await bot.api.messages.send(
                                peer_id=int(peer_id),
                                message=text,
                                random_id=0,
                            )
                            # Небольшая пауза между сообщениями
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            logger.error("Ошибка отправки: %s", e)
        except Exception as e:
            logger.exception("Ошибка в фоновом цикле: %s", e)

        interval = db.get_setting(conn, "check_interval_minutes",
                          CHECK_INTERVAL_MINUTES)
        await asyncio.sleep(int(interval) * 60)


# ---------- Точка входа ----------

async def main():
    logger.info("Запуск бота...")

    # Предварительно загрузим справочники
    global _base_cache
    _base_cache = scheduler.refresh_base_info_if_needed(conn, None)
    if _base_cache is None:
        logger.warning("Не удалось загрузить справочники при старте")

    # Чистим старые снимки (раз в запуск — этого достаточно)
    deleted = db.cleanup_old_snapshots(conn, keep_days=30)
    if deleted:
        logger.info("Очищено старых снимков: %d", deleted)

    # Запускаем фоновый цикл
    asyncio.create_task(background_checker())

    # Запускаем long-polling ВК
    await bot.run_polling()


if __name__ == "__main__":
    asyncio.run(main())