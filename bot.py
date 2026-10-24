"""Точка входа бота.

Запускает:
  1) long-polling ВК (обработка команд от пользователей);
  2) фоновый цикл проверки расписания (scheduler).

Оба работают в одном asyncio-loop.
"""

from __future__ import annotations

import asyncio
import logging
import json

from vkbottle.bot import Bot, Message

from ocr import extract_text as ocr_extract_text
from ocr import is_available as ocr_is_available

from bell_sender import run_bell_checks

from datetime import date as _date

import database as db
import scheduler
from schedule_sender import send_schedule_message
from config import VK_TOKEN, CHECK_INTERVAL_MINUTES, DB_PATH, ADMIN_PEER_ID, COMMAND_COOLDOWN_SECONDS
from bell_detector import detect_bell_schedule
from vk_upload import download_photo, pick_best_photo_size
from command_guard import guarded, check_cooldown
from handlers import (
    cmd_add_group,
    cmd_add_teacher,
    cmd_bell,
    cmd_curator,
    cmd_group,
    cmd_groups,
    cmd_help,
    cmd_interval,
    cmd_metrics,
    cmd_nextweek,
    cmd_remove_group,
    cmd_remove_teacher,
    cmd_set_curator,
    cmd_start,
    cmd_status,
    cmd_subscribers,
    cmd_teacher,
    cmd_teachers,
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


def _parse_payload(payload_raw) -> dict:
    """Безопасно парсит payload из строки в dict."""
    if not payload_raw:
        return {}
    if isinstance(payload_raw, dict):
        return payload_raw
    try:
        return json.loads(payload_raw)
    except (json.JSONDecodeError, TypeError):
        return {}


# Словарь: "команда" -> (функция, аргумент_или_None)
_BUTTON_HANDLERS = {
    # Публичные команды
    "/today": (cmd_today, None),
    "/tomorrow": (cmd_tomorrow, None),
    "/week": (cmd_week, None),
    "/nextweek": (cmd_nextweek, None),
    "/where": (cmd_where, None),
    "/bell": (cmd_bell, ""),
    # Админские команды (без аргументов)
    "/curator": (cmd_curator, None),
    "/status": (cmd_status, None),
    "/unsubscribe": (cmd_unsubscribe, None),
    "/groups": (cmd_groups, None),
    "/teachers": (cmd_teachers, None),
    "/metrics": (cmd_metrics, None),
    "/subscribers": (cmd_subscribers, None),
    # Админские команды (требуют аргументов — передаем "", чтобы вывести подсказку)
    "/add_group": (cmd_add_group, ""),
    "/add_teacher": (cmd_add_teacher, ""),
    "/remove_group": (cmd_remove_group, ""),
    "/remove_teacher": (cmd_remove_teacher, ""),
    "/interval": (cmd_interval, ""),
    "/week_skip": (cmd_week_skip, ""),
    "/use": (cmd_use, ""),
    "/set_curator": (cmd_set_curator, ""),
    "/group": (cmd_group, ""),
    "/teacher": (cmd_teacher, ""),
}


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
                ocr_text = await asyncio.to_thread(ocr_extract_text, image_bytes)
                if ocr_text:
                    logger.info(
                        "OCR done: peer=%s msg=%s chars=%d preview=%r", peer_id, msg_id, len(ocr_text), ocr_text[:80]
                    )
                db.mark_chat_message_ocr_done(conn, peer_id, msg_id)

        # --- Детект bell schedule (text + ocr) ---
        result = detect_bell_schedule(text, ocr_text=ocr_text)
        if not result.is_bell_schedule or result.target_date is None:
            return

        today = _date.today()
        if result.target_date < today:
            logger.warning(
                "Bell schedule определён на %s — это прошлое (сегодня %s), пропускаем. text=%r ocr_preview=%r",
                result.target_date,
                today,
                text[:60],
                ocr_text[:80],
            )
            return

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


@bot.on.message(text=["/start", "начать", "start"])
async def on_start(message: Message):
    await cmd_start(message, conn)


@bot.on.message(text="/help")
@guarded("help", cooldown=COMMAND_COOLDOWN_SECONDS, cancellable=True)
async def on_help(message: Message):
    await cmd_help(message, conn)


@bot.on.message(text="/group <args>")
@guarded("group", cooldown=COMMAND_COOLDOWN_SECONDS, cancellable=True)
async def on_group(message: Message, args: str):
    await cmd_group(message, conn, args)


@bot.on.message(text="/where")
@guarded("where", cooldown=COMMAND_COOLDOWN_SECONDS, cancellable=True)
async def on_where(message: Message):
    await cmd_where(message, conn)


@bot.on.message(text="/today")
@guarded("today", cooldown=COMMAND_COOLDOWN_SECONDS, cancellable=True)
async def on_today(message: Message):
    await cmd_today(message, conn)


@bot.on.message(text="/tomorrow")
@guarded("tomorrow", cooldown=COMMAND_COOLDOWN_SECONDS, cancellable=True)
async def on_tomorrow(message: Message):
    await cmd_tomorrow(message, conn)


@bot.on.message(text="/week")
@guarded("week", cooldown=COMMAND_COOLDOWN_SECONDS, cancellable=True)
async def on_week(message: Message):
    await cmd_week(message, conn)


@bot.on.message(text="/nextweek")
@guarded("nextweek", cooldown=COMMAND_COOLDOWN_SECONDS, cancellable=True)
async def on_nextweek(message: Message):
    await cmd_nextweek(message, conn)


@bot.on.message(text="/status")
@guarded("status", cooldown=COMMAND_COOLDOWN_SECONDS, cancellable=True)
async def on_status(message: Message):
    await cmd_status(message, conn)


@bot.on.message(text=["/use", "/use <args>"])
async def on_use(message: Message, args: str = ""):
    await cmd_use(message, conn, args)


@bot.on.message(text="/unsubscribe")
async def on_unsubscribe(message: Message):
    await cmd_unsubscribe(message, conn)


@bot.on.message(text="/whoami")
async def on_whoami(message: Message):
    await cmd_whoami(message, conn)


@bot.on.message(text="/interval <args>")
async def on_interval(message: Message, args: str):
    await cmd_interval(message, conn, args)


@bot.on.message(text=["/teacher <args>"])
@guarded("teacher", cooldown=COMMAND_COOLDOWN_SECONDS, cancellable=True)
async def on_teacher(message: Message, args: str = ""):
    await cmd_teacher(message, conn, args)


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


# ---------- Multi-tenant: управление преподавателями ----------


@bot.on.message(text=["/add_teacher", "/add_teacher <args>"])
async def on_add_teacher(message: Message, args: str = ""):
    await cmd_add_teacher(message, conn, args)


@bot.on.message(text=["/remove_teacher", "/remove_teacher <args>"])
async def on_remove_teacher(message: Message, args: str = ""):
    await cmd_remove_teacher(message, conn, args)


@bot.on.message(text="/teachers")
async def on_teachers(message: Message):
    await cmd_teachers(message, conn)


# ---------- Multi-tenant: управление группами ----------


@bot.on.message(text=["/add_group", "/add_group <args>"])
async def on_add_group(message: Message, args: str = ""):
    await cmd_add_group(message, conn, args)


@bot.on.message(text=["/remove_group", "/remove_group <args>"])
async def on_remove_group(message: Message, args: str = ""):
    await cmd_remove_group(message, conn, args)


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


@bot.on.raw_event("message_event")
async def on_message_event(event: dict) -> None:
    """Обрабатывает нажатия на Callback-кнопки (Inline)."""
    obj = event.get("object", {})
    if not obj:
        return

    payload = _parse_payload(obj.get("payload"))
    cmd = payload.get("cmd")

    if not cmd:
        return

    # Обязательно: отвечаем VK, чтобы с кнопки исчезла "крутилка" загрузки
    try:
        await bot.api.messages.send_message_event_answer(
            event_id=obj.get("event_id"), user_id=obj.get("user_id"), peer_id=obj.get("peer_id")
        )
    except Exception as e:
        logger.warning("Не удалось ответить на message_event: %s", e)

    class FakeMessage:
        def __init__(self, peer_id, from_id, text, ctx_api):
            self.peer_id = peer_id
            self.from_id = from_id
            self.text = text
            self.ctx_api = ctx_api
            self.payload = payload

        async def answer(self, text, **kwargs):
            await self.ctx_api.messages.send(peer_id=self.peer_id, message=text, random_id=0, **kwargs)

    msg = FakeMessage(peer_id=obj.get("peer_id"), from_id=obj.get("user_id"), text=cmd, ctx_api=bot.api)

    entry = _BUTTON_HANDLERS.get(cmd)
    if not entry:
        logger.warning("Неизвестный payload от кнопки: %s", cmd)
        return

    # Проверка кулдауна для кнопок
    if not check_cooldown(msg.from_id, msg.peer_id, cmd, COMMAND_COOLDOWN_SECONDS):
        return

    func, arg = entry
    try:
        if arg is None:
            await func(msg, conn)
        else:
            await func(msg, conn, arg)
    except Exception as e:
        logger.exception("Ошибка при обработке кнопки %s: %s", cmd, e)


@bot.on.message(
    text=[
        "Сегодня",
        "Завтра",
        "Неделя",
        "След. неделя",
        "Звонки",
        "Где я",
        "Группы",
        "Преподаватели",
        "Статус",
        "Добавить группу",
        "Добавить препода",
        "Интервал",
        "Пропустить неделю",
    ]
)
async def on_reply_button(message: Message):
    """Обрабатывает нажатия на Reply-кнопки (по их тексту)."""
    text = (message.text or "").strip()

    # Словарь: текст кнопки -> (функция, передавать_ли_пустой_аргумент)
    mapping = {
        "Сегодня": (cmd_today, False),
        "Завтра": (cmd_tomorrow, False),
        "Неделя": (cmd_week, False),
        "След. неделя": (cmd_nextweek, False),
        "Звонки": (cmd_bell, True),
        "Где я": (cmd_where, False),
        "Группы": (cmd_groups, False),
        "Преподаватели": (cmd_teachers, False),
        "Статус": (cmd_status, False),
        "Добавить группу": (cmd_add_group, True),
        "Добавить препода": (cmd_add_teacher, True),
        "Интервал": (cmd_interval, True),
        "Пропустить неделю": (cmd_week_skip, True),
    }

    entry = mapping.get(text)
    if not entry:
        return

    # Проверка кулдауна для Reply-кнопок
    if not check_cooldown(message.from_id, message.peer_id, text, COMMAND_COOLDOWN_SECONDS):
        return

    func, needs_arg = entry
    try:
        if needs_arg:
            await func(message, conn, "")
        else:
            await func(message, conn)
    except Exception as e:
        logger.exception("Ошибка при обработке Reply-кнопки %s: %s", text, e)


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
    teachers = db.get_all_teachers(conn, only_active=False)
    if groups or teachers:
        logger.info(
            "БД уже настроена (%d групп, %d преподавателей) — приветствие не отправляется", len(groups), len(teachers)
        )
        return

    text = (
        "👋 Бот запустился.\n\n"
        "Беседа ещё не настроена. Если у вас есть права — "
        "настройте её командой:\n"
        "  /group СП-N №группы (для студентов)\n"
        "  /teacher Фамилия Имя Отчество (для преподавателей)\n\n"
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

            # 3. Обновляем справочники (нужны и для групп, и для преподавателей)
            _base_cache = scheduler.refresh_base_info_if_needed(conn, _base_cache)
            if _base_cache is None:
                logger.warning("Справочники недоступны — пропуск цикла")
            else:
                # --- Проверка групп ---
                groups = db.get_all_groups(conn, only_active=True)
                if not groups:
                    logger.info(">>> checker: активных групп нет")
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

                # --- Проверка преподавателей ---
                teachers = db.get_all_teachers(conn, only_active=True)
                if not teachers:
                    logger.info(">>> checker: активных преподавателей нет")
                else:
                    for t in teachers:
                        teacher_uuid = t["teacher_uuid"]
                        peer_id = t["peer_id"]
                        teacher_name = t["teacher_name"]
                        try:
                            messages = scheduler.run_teacher_check_cycle(conn, _base_cache, teacher_uuid)
                            logger.info(
                                ">>> checker: преподаватель %s (peer=%s), %d сообщений",
                                teacher_name,
                                peer_id,
                                len(messages),
                            )
                            for msg in messages:
                                try:
                                    await send_schedule_message(
                                        bot.api, peer_id, msg, conn=conn, group_uuid=teacher_uuid
                                    )
                                    await asyncio.sleep(2.0)
                                except Exception as e:
                                    logger.error("Ошибка отправки в peer=%s: %s", peer_id, e)
                        except Exception as e:
                            logger.exception("Ошибка цикла для преподавателя %s: %s", teacher_name, e)
        except Exception as e:
            logger.exception("Ошибка в фоновом цикле: %s", e)

        interval = db.get_setting(conn, "check_interval_minutes", CHECK_INTERVAL_MINUTES)
        n_groups = len(db.get_all_groups(conn, only_active=True))
        n_teachers = len(db.get_all_teachers(conn, only_active=True))
        logger.info(
            ">>> checker: цикл завершен (активных групп: %d, преподавателей: %d). Спим %s мин",
            n_groups,
            n_teachers,
            interval,
        )
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
