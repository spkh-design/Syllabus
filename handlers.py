"""Команды ВК-бота.

Все функции — async, потому что vkbottle работает на asyncio.
Каждая функция получает message: vkbottle.bot.Message и работает
с БД через глобальное соединение.

Набор команд:
    /start        — приветствие + краткая инструкция
    /help         — то же, что /start
    /group СП-X yyy — задать группу и подразделение (админ)
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

from schedule_sender import send_schedule_message
from scheduler import ScheduleMessage

import database as db
import schedule_api
from config import is_admin_id
from formatter import format_day_schedule, format_week_schedule, split_message
from bell_detector import extract_date
from bell_sender import prepare_image
from vk_upload import upload_png_to_vk
from group_resolver import is_group_chat, is_personal_chat, parse_peer_arg, resolve_peer_id

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
        logger.info("Отказ в админ-команде: from_id=%s, text=%r", message.from_id, message.text[:80])
        await message.answer("❌ У вас нет прав на эту команду.\n" "Обратитесь к владельцу бота.")
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


def _current_monday(d: date) -> date:
    """Понедельник недели, в которую входит d."""
    return d - timedelta(days=d.weekday())


async def _resolve_group_or_reply(message: Message, conn, args: str = "") -> tuple[int | None, dict | None, str]:
    """Определяет группу для команды и возвращает (peer_id, group, cleaned_args).

    Если определить не удалось — отправляет пользователю подсказку
    и возвращает (None, None, args).

    Если группа определена, но не зарегистрирована — отправляет сообщение
    «беседа не зарегистрирована» и возвращает (peer_id, None, cleaned_args).
    """
    explicit_peer, cleaned_args = parse_peer_arg(args or "")
    peer_id = resolve_peer_id(message, conn, explicit_peer=explicit_peer)

    if peer_id is None:
        await message.answer(
            "⚠️ Не удалось определить группу.\n\n"
            "• В беседе: команда применится к текущей беседе.\n"
            "• В ЛС: укажите беседу явно — `peer=2000000000` — "
            "или выберите активную через `/use 2000000000`."
        )
        return None, None, cleaned_args

    group = db.get_group(conn, peer_id)
    if group is None:
        await message.answer(
            f"⚠️ Беседа `peer={peer_id}` не зарегистрирована.\n\n"
            f"Администратор может добавить её командой:\n"
            f"  /add_group {peer_id} СП-X yyy"
        )
        return peer_id, None, cleaned_args

    return peer_id, group, cleaned_args


# ---------- Публичные команды ----------


async def cmd_start(message: Message, conn) -> None:
    text = (
        "👋 Привет! Я бот расписания СПК.\n\n"
        "Команды:\n"
        "  /where — какая группа сейчас отслеживается\n"
        "  /today — расписание на сегодня\n"
        "  /tomorrow — расписание на завтра\n"
        "  /week — расписание на текущую неделю\n"
        "  /nextweek — расписание на следующую неделю\n"
        "  /groups — список всех групп\n"
        "Админ-команды (только для администраторов):\n"
        "  /add_group peer=N СП-X yyy — зарегистрировать беседу\n"
        "  /remove_group peer=N — удалить регистрацию\n"
        "  /group СП-X yyy — задать группу для текущей беседы\n"
        "  /set_curator ID — назначить куратора\n"
        "  /curator — показать текущего куратора\n"
        "  /interval 30 — интервал проверки\n"
        "  /status — что настроено\n"
        "  /subscribers — подписчики\n"
        "  /week_skip — пометить неделю как объявленную\n"
        "  /unsubscribe — отписаться\n\n"
        "В беседе команды применяются к текущей беседе. "
        "В ЛС — к группе, выбранной через /use или указанной как peer=N."
    )
    await _reply(message, text)


async def cmd_where(message: Message, conn) -> None:
    peer_id, group, _ = await _resolve_group_or_reply(message, conn)
    if group is None:
        return
    await message.answer(f"📍 `peer={peer_id}`\n" f"Группа: {group['division']} / {group['group_name']}")


async def _send_day(message: Message, conn, target: date) -> None:
    peer_id, group, _ = await _resolve_group_or_reply(message, conn)
    if group is None:
        return

    group_uuid = group["group_uuid"]

    base = _load_base(conn)
    if base is None:
        await message.answer("⚠️ Не удалось получить справочники.")
        return

    try:
        lessons = schedule_api.get_group_lessons(base, group_uuid, target)
    except schedule_api.ScheduleAPIError as e:
        logger.error("Ошибка API: %s", e)
        await message.answer("⚠️ Не удалось получить расписание. Попробуйте позже.")
        return

    home = schedule_api.home_territory(base, group_uuid)
    weekday = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][target.weekday()]
    text = f"📅 Расписание на {target.strftime('%d.%m.%Y')} ({weekday})"

    msg = ScheduleMessage(
        kind="day_image",
        text=text,
        fallback_text=format_day_schedule(target, lessons, home),
        target_date=target,
        lessons=lessons,
        home_territory=home,
    )
    await send_schedule_message(message.ctx_api, peer_id, msg, conn=conn, group_uuid=group_uuid)


async def _send_week(message: Message, conn, monday: date) -> None:
    peer_id, group, _ = await _resolve_group_or_reply(message, conn)
    if group is None:
        return

    group_uuid = group["group_uuid"]

    base = _load_base(conn)
    if base is None:
        await message.answer("⚠️ Не удалось получить справочники.")
        return

    week = schedule_api.get_week_lessons(base, group_uuid, monday)
    home = schedule_api.home_territory(base, group_uuid)
    text = (
        f"📅 Расписание на неделю "
        f"с {monday.strftime('%d.%m.%Y')} "
        f"по {(monday + timedelta(days=5)).strftime('%d.%m.%Y')}"
    )

    msg = ScheduleMessage(
        kind="week_image",
        text=text,
        fallback_text=format_week_schedule(monday, week, home),
        week_start=monday,
        week=week,
        home_territory=home,
    )
    await send_schedule_message(message.ctx_api, peer_id, msg, conn=conn, group_uuid=group_uuid)


async def cmd_today(message: Message, conn) -> None:
    await _send_day(message, conn, date.today())


async def cmd_tomorrow(message: Message, conn) -> None:
    await _send_day(message, conn, date.today() + timedelta(days=1))


async def cmd_week(message: Message, conn) -> None:
    monday = _current_monday(date.today())
    await _send_week(message, conn, monday)


async def cmd_nextweek(message: Message, conn) -> None:
    monday = _current_monday(date.today()) + timedelta(days=7)
    await _send_week(message, conn, monday)


async def cmd_status(message: Message, conn) -> None:
    """Статус группы, к которой относится команда. Только для админов."""
    if not await _require_admin(message):
        return

    peer_id, group, _ = await _resolve_group_or_reply(message, conn)

    lines = ["📊 Статус:"]

    if group is None:
        lines.append("  Группа: не определена или не зарегистрирована")
    else:
        base_age = db.base_info_age_hours(conn)
        lines.append(f"  peer_id: {peer_id}")
        lines.append(f"  Группа: {group['division']} / {group['group_name']}")
        curator = group.get("curator_id")
        lines.append(f"  Куратор: {curator if curator else '—'}")
        lines.append(f"  Активна: {'да' if group['is_active'] else 'нет'}")
        if base_age is None:
            lines.append("  Справочники: не загружены")
        else:
            lines.append(f"  Справочники: обновлены {base_age:.1f} ч назад")

    # Всего групп в системе
    all_groups = db.get_all_groups(conn, only_active=False)
    lines.append(f"  Всего групп: {len(all_groups)}")

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
    """Регистрация/обновление группы.

    Синтаксис:
        В беседе:        /group СП-X yyy
        В ЛС:            /group СП-X yyy peer=2000000000
    """
    if not await _require_admin(message):
        return

    # Парсим peer=N
    explicit_peer, cleaned_args = parse_peer_arg(args or "")
    peer_id = resolve_peer_id(message, conn, explicit_peer=explicit_peer)

    if peer_id is None:
        await message.answer(
            "❌ Не удалось определить беседу.\n\n"
            "В ЛС: укажите явно — `/group СП-X yyy peer=2000000000`\n"
            "или выберите через `/use 2000000000`."
        )
        return

    parts = cleaned_args.strip().split()
    if len(parts) != 2:
        await message.answer("❌ Формат: /group СП-X yyy\n" "В ЛС дополнительно: /group СП-X yyy peer=2000000000")
        return

    division, group_name = parts
    base = _load_base(conn)
    if base is None:
        await message.answer("⚠️ Не удалось получить справочники. Попробуйте позже.")
        return

    found = schedule_api.find_group(base, group_name, division)
    if found is None:
        await message.answer(
            f"❌ Группа «{group_name}» в подразделении «{division}» не найдена.\n" f"Проверьте номер и подразделение."
        )
        return

    db.add_group(conn, peer_id=peer_id, group_uuid=found["id"], group_name=found["name"], division=division)

    await message.answer(
        f"✅ Настроено для `peer={peer_id}`:\n"
        f"   Группа: {found['name']} (курс {found['curse']})\n"
        f"   Подразделение: {division}\n\n"
        f"Теперь я буду следить за изменениями расписания "
        f"и присылать уведомления в эту беседу."
    )


async def cmd_unsubscribe(message: Message, conn) -> None:
    """Удаляет регистрацию группы, к которой относится команда."""
    if not await _require_admin(message):
        return

    peer_id, group, _ = await _resolve_group_or_reply(message, conn)
    if group is None:
        return

    db.remove_group(conn, peer_id)
    await message.answer(f"✅ Отписка выполнена для `peer={peer_id}`.\n" f"Чтобы снова подписаться: /group СП-X yyy")


async def cmd_interval(message: Message, conn, args: str) -> None:
    """Меняет интервал проверки (в минутах). Только для админов."""
    if not await _require_admin(message):
        return

    args = args.strip()
    if not args.isdigit():
        await message.answer("❌ Формат: /interval 15\n" "Число — интервал в минутах (минимум 5).")
        return

    minutes = int(args)
    if minutes < 5:
        await message.answer("❌ Минимум 5 минут.")
        return
    if minutes > 1440:
        await message.answer("❌ Максимум 1440 минут (сутки).")
        return

    db.set_setting(conn, "check_interval_minutes", minutes)
    await message.answer(f"✅ Интервал проверки: {minutes} минут.\n" f"Изменение вступит в силу со следующего цикла.")


async def cmd_test(message: Message, conn) -> None:
    """Тестовое уведомление + расписание на сегодня. Только для админов."""
    if not await _require_admin(message):
        return

    peer_id, group, _ = await _resolve_group_or_reply(message, conn)
    if group is None:
        return

    await message.answer(
        f"🔔 Тестовое уведомление. Если вы его видите — бот работает.\n" f"Группа: {group['group_name']}"
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

    groups = db.get_all_groups(conn, only_active=False)
    if not groups:
        await message.answer("⚠️ Нет зарегистрированных групп.")
        return

    lines = [f"👥 Группы ({len(groups)}):"]
    for g in groups:
        status = "✅" if g["is_active"] else "⏸"
        curator = g.get("curator_id")
        curator_str = f", куратор {curator}" if curator else ""
        lines.append(f"  {status} `peer={g['peer_id']}` — " f"{g['division']} / {g['group_name']}{curator_str}")

    await message.answer("\n".join(lines))


async def cmd_week_skip(message: Message, conn, args: str = "") -> None:
    """Помечает неделю как «объявленную». Только для админов.

    args — опциональная дата в формате DD.MM.YYYY + опционально peer=N.
    """
    if not await _require_admin(message):
        return

    explicit_peer, cleaned_args = parse_peer_arg(args or "")
    peer_id = resolve_peer_id(message, conn, explicit_peer=explicit_peer)

    if peer_id is None:
        await message.answer(
            "❌ Не удалось определить беседу.\n"
            "В ЛС: укажите явно — `/week_skip peer=2000000000`\n"
            "или `/week_skip 21.09.2026 peer=2000000000`."
        )
        return

    group = db.get_group(conn, peer_id)
    if group is None:
        await message.answer(f"⚠️ Беседа `peer={peer_id}` не зарегистрирована.\n" f"Сначала: /group СП-X yyy")
        return

    group_uuid = group["group_uuid"]
    cleaned_args = cleaned_args.strip()

    if cleaned_args:
        try:
            from datetime import datetime as _dt

            d = _dt.strptime(cleaned_args, "%d.%m.%Y").date()
        except ValueError:
            await message.answer(
                "❌ Формат: /week_skip или /week_skip 21.09.2026\n" "Без аргумента — для следующей недели."
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
        f"как «объявленная» для `peer={peer_id}`.\n"
        f"Бот не будет присылать полное расписание этой недели "
        f"автоматически. Если хотите увидеть его сейчас — /week."
    )


# ============================================================
# Multi-tenant: управление группами
# ============================================================


async def cmd_add_group(message: Message, conn, args: str) -> None:
    """Регистрирует беседу как группу.

    Синтаксис:
        В беседе:  /add_group СП-X yyy
        В ЛС:      /add_group peer=2000000000 СП-X yyy
    """
    if not await _require_admin(message):
        return

    explicit_peer, cleaned_args = parse_peer_arg(args or "")

    # В беседе — используем текущую, если peer не задан явно.
    # В ЛС — обязателен explicit_peer (не сессия), чтобы случайно
    # не зарегистрировать не ту беседу.
    if explicit_peer is not None:
        peer_id = explicit_peer
    elif is_group_chat(message.peer_id):
        peer_id = message.peer_id
    else:
        await message.answer("❌ В ЛС нужно указать беседу явно:\n" "  /add_group peer=2000000000 СП-X yyy")
        return

    parts = cleaned_args.strip().split()
    if len(parts) != 2:
        await message.answer("❌ Формат: /add_group СП-X yyy\n" "В ЛС: /add_group peer=2000000000 СП-X yyy")
        return

    division, group_name = parts
    base = _load_base(conn)
    if base is None:
        await message.answer("⚠️ Не удалось получить справочники. Попробуйте позже.")
        return

    found = schedule_api.find_group(base, group_name, division)
    if found is None:
        await message.answer(f"❌ Группа «{group_name}» в подразделении «{division}» не найдена.")
        return

    db.add_group(conn, peer_id=peer_id, group_uuid=found["id"], group_name=found["name"], division=division)

    await message.answer(
        f"✅ Группа зарегистрирована:\n"
        f"   peer_id: {peer_id}\n"
        f"   Группа: {found['name']} (курс {found['curse']})\n"
        f"   Подразделение: {division}\n\n"
        f"Бот будет присылать уведомления в эту беседу."
    )


async def cmd_remove_group(message: Message, conn, args: str) -> None:
    """Удаляет регистрацию группы.

    Синтаксис:
        В беседе:  /remove_group
        В ЛС:      /remove_group peer=2000000000
    """
    if not await _require_admin(message):
        return

    explicit_peer, _ = parse_peer_arg(args or "")

    if explicit_peer is not None:
        peer_id = explicit_peer
    elif is_group_chat(message.peer_id):
        peer_id = message.peer_id
    else:
        await message.answer("❌ В ЛС нужно указать беседу:\n" "  /remove_group peer=2000000000")
        return

    group = db.get_group(conn, peer_id)
    if group is None:
        await message.answer(f"⚠️ Беседа `peer={peer_id}` не зарегистрирована.")
        return

    db.remove_group(conn, peer_id)
    await message.answer(
        f"✅ Группа `peer={peer_id}` ({group['division']} / {group['group_name']}) " f"удалена из бота."
    )


async def cmd_use(message: Message, conn, args: str) -> None:
    """Выбор активной группы для команд в ЛС.

    Синтаксис: /use 2000000000
    """
    if not is_group_chat(message.peer_id) and not is_personal_chat(message.peer_id):
        # Теоретически сюда не попадём, но на всякий случай
        pass

    if is_group_chat(message.peer_id):
        await message.answer(
            "ℹ️ Команда `/use` работает только в личных сообщениях.\n"
            "В беседе команды применяются к текущей беседе автоматически."
        )
        return

    peer_id, cleaned = parse_peer_arg(args or "")
    # Если написали просто `/use 2000000000` — peer_id будет None,
    # а cleaned = "2000000000". Разбираем вручную.
    if peer_id is None:
        cleaned = cleaned.strip()
        if not cleaned.isdigit():
            await message.answer(
                "❌ Формат: /use 2000000000\n" "Число — ID беседы (можно узнать командой /whoami в этой беседе)."
            )
            return
        peer_id = int(cleaned)

    group = db.get_group(conn, peer_id)
    if group is None:
        await message.answer(
            f"⚠️ Беседа `peer={peer_id}` не зарегистрирована.\n"
            f"Сначала админ должен добавить её: /add_group peer={peer_id} СП-X yyy"
        )
        return

    db.set_user_session(conn, message.from_id, peer_id)
    await message.answer(
        f"✅ Активная группа: `peer={peer_id}`\n"
        f"   {group['division']} / {group['group_name']}\n\n"
        f"Теперь команды `/today`, `/week`, `/status` и т.д. будут "
        f"применяться к этой группе."
    )


async def cmd_groups(message: Message, conn) -> None:
    """Список всех зарегистрированных групп. Только для админов."""
    if not await _require_admin(message):
        return

    groups = db.get_all_groups(conn, only_active=False)
    if not groups:
        await message.answer("📋 Групп нет.")
        return

    active_session = db.get_user_session(conn, message.from_id) if is_personal_chat(message.peer_id) else None

    lines = [f"📋 Группы ({len(groups)}):"]
    for g in groups:
        status = "✅" if g["is_active"] else "⏸"
        marker = " 👈" if active_session == g["peer_id"] else ""
        curator = g.get("curator_id")
        curator_str = f", куратор {curator}" if curator else ""
        lines.append(f"  {status} `peer={g['peer_id']}` — " f"{g['division']} / {g['group_name']}{curator_str}{marker}")

    if is_personal_chat(message.peer_id):
        lines.append("")
        lines.append("Выбрать группу: /use <peer_id>")

    await message.answer("\n".join(lines))


# ============================================================
# Multi-tenant: куратор
# ============================================================


async def _resolve_curator_id(ctx_api, arg: str) -> tuple[int | None, str]:
    """Преобразует `@username` или числовой ID в (user_id, error_message).

    Returns:
        (user_id, "") при успехе,
        (None, "текст ошибки") при неудаче.
    """
    arg = arg.strip().lstrip("@")
    if not arg:
        return None, "Пустой аргумент."

    if arg.isdigit():
        return int(arg), ""

    # Пытаемся резолвить как username через VK API
    try:
        users = await ctx_api.users.get(user_ids=[arg])
    except Exception as e:
        return None, f"Не удалось найти пользователя «{arg}»: {e}"

    if not users:
        return None, f"Пользователь «{arg}» не найден."

    # vkbottle возвращает список объектов с атрибутом id
    user = users[0]
    uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    if uid is None:
        return None, f"VK API не вернул ID для «{arg}»."

    return int(uid), ""


async def cmd_set_curator(message: Message, conn, args: str) -> None:
    """Назначает куратора группы.

    Синтаксис:
        В беседе:  /set_curator 123456
                   /set_curator @username
        В ЛС:      /set_curator 123456 peer=2000000000
    """
    if not await _require_admin(message):
        return

    explicit_peer, cleaned_args = parse_peer_arg(args or "")
    peer_id = resolve_peer_id(message, conn, explicit_peer=explicit_peer)

    if peer_id is None:
        await message.answer(
            "❌ Не удалось определить группу.\n"
            "В ЛС: укажите явно — `/set_curator 123456 peer=2000000000`\n"
            "или сначала выберите через `/use 2000000000`."
        )
        return

    group = db.get_group(conn, peer_id)
    if group is None:
        await message.answer(f"⚠️ Беседа `peer={peer_id}` не зарегистрирована.")
        return

    arg = cleaned_args.strip()
    if not arg:
        await message.answer(
            "❌ Формат: /set_curator 123456\n"
            "Или: /set_curator @username\n\n"
            "Узнать VK ID куратора: пусть он напишет /whoami в этой беседе."
        )
        return

    curator_id, error = await _resolve_curator_id(message.ctx_api, arg)
    if curator_id is None:
        await message.answer(f"❌ {error}")
        return

    db.set_group_curator(conn, peer_id, curator_id)
    await message.answer(f"✅ Куратор для `peer={peer_id}` установлен: `{curator_id}`")


async def cmd_curator(message: Message, conn) -> None:
    """Показывает текущего куратора группы."""
    peer_id, group, _ = await _resolve_group_or_reply(message, conn)
    if group is None:
        return

    curator = group.get("curator_id")
    if curator is None:
        await message.answer(
            f"👤 У группы `peer={peer_id}` куратор не назначен.\n" f"Назначить: /set_curator <ID или @username>"
        )
        return

    # Пытаемся получить имя через VK API
    try:
        users = await message.ctx_api.users.get(user_ids=[curator])
        if users:
            u = users[0]
            name = f"{u.first_name} {u.last_name}".strip()
            await message.answer(f"👤 Куратор группы `peer={peer_id}`:\n" f"   {name} ([id{curator}|{u.first_name}])")
            return
    except Exception as e:
        logger.warning("Не удалось получить имя куратора %s: %s", curator, e)

    await message.answer(f"👤 Куратор группы `peer={peer_id}`: `{curator}`\n" f"(имя получить не удалось)")


# ============================================================
# Multi-tenant: расписание звонков
# ============================================================


async def cmd_bell(message: Message, conn, args: str) -> None:
    """Показать расписание звонков на дату.

    Синтаксис:
        /bell 21.10.26
        /bell 21.10.2026
        /bell 21 октября
        /bell 21 октября 2026
        /bell 21.10.26 peer=2000000000  (в ЛС)
    """
    peer_id, group, cleaned_args = await _resolve_group_or_reply(message, conn, args or "")
    if group is None:
        return

    date_str = cleaned_args.strip()
    if not date_str:
        await message.answer(
            "❌ Формат: /bell 21.10.26\n" "Также: /bell 21.10.2026, /bell 21 октября, /bell 21 октября 2026"
        )
        return

    today = date.today()
    target = extract_date(date_str, today=today)
    if target is None:
        await message.answer(
            "❌ Не удалось распознать дату.\n" "Форматы: /bell 21.10.26, /bell 21.10.2026, /bell 21 октября"
        )
        return

    diff = (target - today).days
    if diff < 0:
        await message.answer("⚠️ День уже прошёл.")
        return

    if diff > 30:
        await message.answer(f"❌ Слишком далеко: {target.strftime('%d.%m.%Y')}.\n" f"Максимум — 30 дней вперёд.")
        return

    date_label = target.strftime("%d.%m.%y")  # 21.10.26
    bs = db.get_bell_schedule(conn, peer_id, target)

    # Case C: записи нет → обычное расписание
    if bs is None:
        await message.ctx_api.messages.send(
            peer_id=peer_id,
            message=(f"📅 Расписание звонков на {date_label}\n" f"Без изменений — обычное расписание."),
            random_id=0,
        )
        return

    # Case A: есть картинка
    logger.info("cmd_bell: peer_id=%s, target=%s, has_image=%s", peer_id, target, bool(bs.get("image_bytes")))
    if bs.get("image_bytes"):
        text = f"🔔 Расписание звонков на {date_label}"
        try:
            prepared, content_type, filename = prepare_image(bs["image_bytes"])
            attachment = await upload_png_to_vk(
                message.ctx_api, prepared, peer_id=peer_id, content_type=content_type, filename=filename
            )
            await message.ctx_api.messages.send(peer_id=peer_id, message=text, attachment=attachment, random_id=0)
        except Exception as e:
            logger.exception("cmd_bell: не удалось отправить картинку: %s", e)
            # Fallback: текст + caption
            caption = (bs.get("caption") or "").strip()
            fallback = text
            if caption:
                fallback += f"\n\n{caption}"
            await message.ctx_api.messages.send(peer_id=peer_id, message=fallback, random_id=0)
        return

    # Case B: есть запись, но без картинки
    caption = (bs.get("caption") or "").strip() or "(текст сообщения отсутствует)"
    await message.ctx_api.messages.send(
        peer_id=peer_id, message=f"Расписание звонков на {date_label}.\n{caption}", random_id=0
    )
