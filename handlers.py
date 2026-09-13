"""Команды ВК-бота.

Все функции — async, потому что vkbottle работает на asyncio.
Каждая функция получает message: vkbottle.bot.Message и работает
с БД через глобальное соединение.

Набор команд:
    /start        — приветствие + краткая инструкция
    /help         — то же, что /start
    /group СП-4 419 — задать группу и подразделение (админ)
    /where        — показать текущую настройку (публичная)
    /today        — расписание на сегодня (публичная)
    /tomorrow     — расписание на завтра (публичная)
    /week         — расписание на следующую неделю (публичная)
    /status       — что настроено, когда последняя проверка (публичная)
    /unsubscribe  — сбросить настройки (админ)
    /interval N   — интервал проверки (админ)
    /test         — тестовое уведомление (админ)
    /metrics      — счётчики (админ)
    /subscribers  — кто получает уведомления (админ)
    /week_skip    — пометить неделю как «объявленную» (админ)
    /whoami       — узнать свой ID (публичная, скрытая)

Скрытые команды (не упоминаются в /help):
    /whoami
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from vkbottle.bot import Message

import database as db
import schedule_api
from config import is_admin_id
from formatter import (
    format_day_schedule,
    format_week_schedule,
    split_message,
)

logger = logging.getLogger(__name__)


# ---------- Утилиты ----------

def _peer_id(message: Message) -> int:
    """Возвращает peer_id сообщения."""
    return message.peer_id


def is_admin(message: Message) -> bool:
    """True, если автор сообщения — админ.

    Проверяется message.from_id — ID конкретного пользователя,
    который написал сообщение. В беседе from_id != peer_id.
    """
    return is_admin_id(message.from_id)


async def _require_admin(message: Message) -> bool:
    """Проверяет права. Если не админ — отвечает и возвращает False.

    Использование:
        if not await _require_admin(message):
            return
        # ... остальная логика ...
    """
    if not is_admin(message):
        logger.info("Отказ в админ-команде: from_id=%s, text=%r",
                    message.from_id, message.text[:80])
        await message.answer(
            "❌ У вас нет прав на эту команду.\n"
            "Обратитесь к владельцу бота."
        )
        return False
    return True


def _load_base(conn) -> dict | None:
    """Загружает справочники из БД или из API."""
    base = db.get_base_info(conn)
    if base is None:
        try:
            base = schedule_api.fetch_base_info()
            db.save_base_info(conn, base)
        except schedule_api.ScheduleAPIError as e:
            logger.error("Не удалось получить справочники: %s", e)
            return None
    return base


async def _reply(message: Message, text: str) -> None:
    """Отправляет текст, нарезая по лимиту ВК."""
    for part in split_message(text):
        await message.answer(part)


# ---------- Публичные команды ----------

async def cmd_start(message: Message, conn) -> None:
    text = (
        "👋 Привет! Я бот расписания СПК.\n\n"
        "Команды:\n"
        "  /where — какая группа сейчас отслеживается\n"
        "  /today — расписание на сегодня\n"
        "  /tomorrow — расписание на завтра\n"
        "  /week — расписание на следующую неделю\n"
        "  /status — что настроено\n\n"
        "Админ-команды (только для администраторов):\n"
        "  /group СП-4 419 — задать группу и подразделение\n"
        "  /interval 30 — интервал проверки\n"
        "  /test — тестовое уведомление\n"
        "  /metrics — счётчики\n"
        "  /subscribers — подписчики\n"
        "  /week_skip — пометить неделю как объявленную\n"
        "  /unsubscribe — отписаться\n\n"
        "После настройки я буду присылать сюда уведомления "
        "об изменениях расписания."
    )
    await _reply(message, text)


async def cmd_where(message: Message, conn) -> None:
    group_name = db.get_setting(conn, "group_name")
    division = db.get_setting(conn, "division")
    if not group_name:
        await message.answer(
            "⚠️ Группа ещё не настроена.\n"
            "Попросите администратора выполнить /group СП-4 419"
        )
        return
    await message.answer(
        f"📍 Текущая настройка: {division} / {group_name}"
    )


async def cmd_today(message: Message, conn) -> None:
    await _send_day(message, conn, date.today())


async def cmd_tomorrow(message: Message, conn) -> None:
    await _send_day(message, conn, date.today() + timedelta(days=1))


async def _send_day(message: Message, conn, target: date) -> None:
    group_uuid = db.get_setting(conn, "group_uuid")
    if not group_uuid:
        await message.answer("⚠️ Группа ещё не настроена.")
        return

    base = _load_base(conn)
    if base is None:
        await message.answer("⚠️ Не удалось получить справочники.")
        return

    try:
        lessons = schedule_api.get_group_lessons(base, group_uuid, target)
    except schedule_api.ScheduleAPIError as e:
        logger.error("Ошибка API: %s", e)
        await message.answer("⚠️ Не удалось получить расписание. "
                             "Попробуйте позже.")
        return

    home = schedule_api.home_territory(base, group_uuid)
    text = format_day_schedule(target, lessons, home)
    await _reply(message, text)


async def cmd_week(message: Message, conn) -> None:
    group_uuid = db.get_setting(conn, "group_uuid")
    if not group_uuid:
        await message.answer("⚠️ Группа ещё не настроена.")
        return

    base = _load_base(conn)
    if base is None:
        await message.answer("⚠️ Не удалось получить справочники.")
        return

    today = date.today()
    days_ahead = (0 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    next_monday = today + timedelta(days=days_ahead)

    week = schedule_api.get_week_lessons(base, group_uuid, next_monday)
    home = schedule_api.home_territory(base, group_uuid)
    text = format_week_schedule(next_monday, week, home)
    await _reply(message, text)


async def cmd_status(message: Message, conn) -> None:
    group_name = db.get_setting(conn, "group_name")
    division = db.get_setting(conn, "division")
    peer_id = db.get_setting(conn, "peer_id")
    base_age = db.base_info_age_hours(conn)

    lines = ["📊 Статус:"]
    lines.append(f"  Группа: {division} / {group_name}" if group_name
                 else "  Группа: не настроена")
    lines.append(f"  Peer ID: {peer_id}" if peer_id else "  Peer ID: —")
    if base_age is None:
        lines.append("  Справочники: не загружены")
    else:
        lines.append(f"  Справочники: обновлены {base_age:.1f} ч назад")
    lines.append(f"  Сейчас: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    await message.answer("\n".join(lines))


async def cmd_whoami(message: Message, conn) -> None:
    """Показывает ID пользователя и чата. Для настройки .env.

    Скрытая команда — не упоминается в /help.
    """
    from_id = message.from_id
    peer_id = message.peer_id
    admin = "✅ да" if is_admin(message) else "❌ нет"

    text = (
        "🆔 Информация о вас:\n"
        f"  Ваш user ID (from_id): {from_id}\n"
        f"  ID этого чата (peer_id): {peer_id}\n"
        f"  Вы админ: {admin}\n\n"
    )
    await message.answer(text)


# ---------- Админские команды ----------

async def cmd_group(message: Message, conn, args: str) -> None:
    """args — '<подразделение> <группа>'. Только для админов."""
    if not await _require_admin(message):
        return

    parts = args.strip().split()
    if len(parts) != 2:
        await message.answer(
            "❌ Формат: /group СП-4 419\n"
            "Первое — подразделение (СП-1..СП-5), второе — номер группы."
        )
        return

    division, group_name = parts
    base = _load_base(conn)
    if base is None:
        await message.answer("⚠️ Не удалось получить справочники. "
                             "Попробуйте позже.")
        return

    group = schedule_api.find_group(base, group_name, division)
    if group is None:
        await message.answer(
            f"❌ Группа «{group_name}» в подразделении «{division}» не найдена.\n"
            f"Проверьте номер и подразделение."
        )
        return

    peer_id = _peer_id(message)
    db.set_setting(conn, "peer_id", str(peer_id))
    db.set_setting(conn, "group_name", group["name"])
    db.set_setting(conn, "group_uuid", group["id"])
    db.set_setting(conn, "division", division)

    await message.answer(
        f"✅ Настроено:\n"
        f"   Группа: {group['name']} (курс {group['curse']})\n"
        f"   Подразделение: {division}\n\n"
        f"Теперь я буду следить за изменениями расписания "
        f"и присылать уведомления сюда."
    )


async def cmd_unsubscribe(message: Message, conn) -> None:
    if not await _require_admin(message):
        return
    for key in ("peer_id", "group_uuid", "group_name", "division"):
        db.delete_setting(conn, key)
    await message.answer(
        "✅ Отписка выполнена. Чтобы снова подписаться: /group СП-4 419"
    )


async def cmd_interval(message: Message, conn, args: str) -> None:
    """Меняет интервал проверки (в минутах). Только для админов."""
    if not await _require_admin(message):
        return

    args = args.strip()
    if not args.isdigit():
        await message.answer(
            "❌ Формат: /interval 15\n"
            "Число — интервал в минутах (минимум 5)."
        )
        return

    minutes = int(args)
    if minutes < 5:
        await message.answer("❌ Минимум 5 минут.")
        return
    if minutes > 1440:
        await message.answer("❌ Максимум 1440 минут (сутки).")
        return

    db.set_setting(conn, "check_interval_minutes", minutes)
    await message.answer(
        f"✅ Интервал проверки: {minutes} минут.\n"
        f"Изменение вступит в силу со следующего цикла."
    )


async def cmd_test(message: Message, conn) -> None:
    """Тестовое уведомление + расписание на сегодня. Только для админов."""
    if not await _require_admin(message):
        return

    group_name = db.get_setting(conn, "group_name")
    if not group_name:
        await message.answer("⚠️ Сначала настройте группу: /group СП-4 419")
        return

    await message.answer(
        "🔔 Тестовое уведомление. Если вы его видите — бот работает.\n"
        f"Группа: {group_name}"
    )
    await cmd_today(message, conn)


async def cmd_metrics(message: Message, conn) -> None:
    """Счётчики. Только для админов."""
    if not await _require_admin(message):
        return

    checks = db.get_setting(conn, "metrics_checks", 0)
    notifications = db.get_setting(conn, "metrics_notifications", 0)
    errors = db.get_setting(conn, "metrics_errors", 0)
    last_check = db.get_setting(conn, "metrics_last_check") or "—"

    text = (
        "📊 Метрики:\n"
        f"  Проверок: {checks}\n"
        f"  Уведомлений: {notifications}\n"
        f"  Ошибок: {errors}\n"
        f"  Последняя проверка: {last_check}"
    )
    await message.answer(text)


async def cmd_subscribers(message: Message, conn) -> None:
    """Кто получает уведомления. Только для админов."""
    if not await _require_admin(message):
        return

    peer_id = db.get_setting(conn, "peer_id")
    group_name = db.get_setting(conn, "group_name")
    division = db.get_setting(conn, "division")

    if not peer_id:
        await message.answer("⚠️ Нет активных подписчиков.")
        return

    text = (
        "👥 Подписчики:\n"
        f"  peer_id={peer_id}\n"
        f"  группа: {division} / {group_name}"
    )
    await message.answer(text)


async def cmd_week_skip(message: Message, conn, args: str = "") -> None:
    """Ставит флаг «неделя объявлена». Только для админов.

    args — опциональная дата в формате DD.MM.YYYY. Если пусто —
    берётся следующая неделя.
    """
    if not await _require_admin(message):
        return

    group_uuid = db.get_setting(conn, "group_uuid")
    if not group_uuid:
        await message.answer("⚠️ Сначала настройте группу: /group СП-4 419")
        return

    args = (args or "").strip()

    if args:
        try:
            from datetime import datetime as _dt
            d = _dt.strptime(args, "%d.%m.%Y").date()
        except ValueError:
            await message.answer(
                "❌ Формат: /week_skip или /week_skip 21.09.2026\n"
                "Без аргумента — для следующей недели."
            )
            return
    else:
        today = date.today()
        days_ahead = (0 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        d = today + timedelta(days=days_ahead)

    week_start = d - timedelta(days=d.weekday())

    db.set_week_announced(conn, group_uuid, week_start, is_full=False)
    await message.answer(
        f"✅ Неделя с {week_start.strftime('%d.%m.%Y')} помечена "
        f"как «объявленная».\n"
        f"Бот не будет присылать полное расписание этой недели "
        f"автоматически. Если хотите увидеть его сейчас — /week."
    )