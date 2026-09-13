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
from config import (
    VK_TOKEN,
    CHECK_INTERVAL_MINUTES,
    DB_PATH,
    ADMIN_PEER_ID,
)
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
    cmd_week_skip,
    cmd_where,
    cmd_whoami,
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


@bot.on.message()
async def on_unknown(message: Message):
    await message.answer(
        "🤔 Не понимаю команду.\n"
        "Напишите /help, чтобы увидеть список."
    )

# ---------- Фоновый цикл проверки ----------

async def send_startup_greeting(
    conn,
    admin_peer_id: int | None = ADMIN_PEER_ID,
    send_func=None,
) -> None:
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

    group_uuid = db.get_setting(conn, "group_uuid")
    if group_uuid:
        logger.info("БД уже настроена — приветствие не отправляется")
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
    """Раз в CHECK_INTERVAL_MINUTES проверяет расписание."""
    global _base_cache

    logger.info(">>> background_checker: стартовал")

    while True:
        try:
            group_uuid = db.get_setting(conn, "group_uuid")
            peer_id = db.get_setting(conn, "peer_id")
            logger.info(">>> checker: group_uuid=%r, peer_id=%r",
                        group_uuid, peer_id)

            if group_uuid and peer_id:
                _base_cache = scheduler.refresh_base_info_if_needed(
                    conn, _base_cache,
                )
                if _base_cache is None:
                    logger.warning("Справочники недоступны — пропуск цикла")
                else:
                    messages = scheduler.run_check_cycle(
                        conn, _base_cache, str(group_uuid),
                    )
                    logger.info(">>> checker: получено %d сообщений",
                                len(messages))
                    for text in messages:
                        try:
                            await bot.api.messages.send(
                                peer_id=int(peer_id),
                                message=text,
                                random_id=0,
                            )
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            logger.error("Ошибка отправки: %s", e)
            else:
                logger.warning(
                    ">>> checker: нет настроек — group_uuid=%r, peer_id=%r",
                    group_uuid, peer_id,
                )
        except Exception as e:
            logger.exception("Ошибка в фоновом цикле: %s", e)

        interval = db.get_setting(conn, "check_interval_minutes",
                                   CHECK_INTERVAL_MINUTES)
        logger.info(">>> checker: спим %s мин", interval)
        await asyncio.sleep(int(interval) * 60)

# ---------- Точка входа ----------

async def main():
    logger.info("Запуск бота...")

    global _base_cache
    _base_cache = scheduler.refresh_base_info_if_needed(conn, None)
    if _base_cache is None:
        logger.warning("Не удалось загрузить справочники при старте")

    # Чистим старые снимки
    deleted = db.cleanup_old_snapshots(conn, keep_days=30)
    if deleted:
        logger.info("Очищено старых снимков: %d", deleted)

    # Чистим старые объявленные недели
    deleted_weeks = db.cleanup_old_announced_weeks(conn, keep_days=30)
    if deleted_weeks:
        logger.info("Очищено старых записей о неделях: %d", deleted_weeks)

    # Приветствие — до запуска polling, чтобы точно ушло
    await send_startup_greeting(conn)

    # Запускаем фоновый цикл
    asyncio.create_task(background_checker())

    # Запускаем long-polling ВК
    await bot.run_polling()


if __name__ == "__main__":
    asyncio.run(main())