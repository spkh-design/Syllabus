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

import re
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


async def _resolve_entity_or_reply(message: Message, conn, args: str = "") -> tuple[int | None, dict | None, str]:
    """Определяет группу или преподавателя для команды и возвращает (peer_id, entity, cleaned_args)."""
    explicit_peer, cleaned_args = parse_peer_arg(args or "")
    peer_id = resolve_peer_id(message, conn, explicit_peer=explicit_peer)

    if peer_id is None:
        await message.answer(
            "⚠️ Не удалось определить беседу.\n\n"
            "• В беседе: команда применится к текущей беседе.\n"
            "• В ЛС: укажите беседу явно — `peer=2000000000` — "
            "или выберите активную через `/use 2000000000`."
        )
        return None, None, cleaned_args

    entity = db.get_entity(conn, peer_id)
    if entity is None:
        await message.answer(
            f"⚠️ Беседа `peer={peer_id}` не зарегистрирована.\n\n"
            f"Администратор может добавить группу:\n"
            f"  /add_group {peer_id} СП-X yyy\n"
            f"Или преподавателя:\n"
            f"  /teacher {peer_id} Фамилия Имя Отчество"
        )
        return peer_id, None, cleaned_args

    return peer_id, entity, cleaned_args


# ---------- Публичные команды ----------


async def cmd_start(message: Message, conn) -> None:
    text = (
        "👋 Привет! Я бот расписания СПК.\n\n"
        "Команды:\n"
        "  /where — какая беседа сейчас отслеживается\n"
        "  /today — расписание на сегодня\n"
        "  /tomorrow — расписание на завтра\n"
        "  /week — расписание на текущую неделю\n"
        "  /nextweek — расписание на следующую неделю\n"
        "  /groups — список всех групп\n"
        "  /teachers — список всех преподавателей\n"
        "Админ-команды (только для администраторов):\n"
        "  /add_group peer=N СП-X yyy — зарегистрировать беседу группы\n"
        "  /remove_group peer=N — удалить регистрацию группы\n"
        "  /group СП-X yyy — задать группу для текущей беседы\n"
        "  /add_teacher peer=N ФИО — зарегистрировать беседу преподавателя\n"
        "  /remove_teacher peer=N — удалить регистрацию преподавателя\n"
        "  /teacher ФИО — задать преподавателя для текущей беседы\n"
        "  /set_curator ID — назначить куратора (только для групп)\n"
        "  /curator — показать текущего куратора\n"
        "  /interval 30 — интервал проверки\n"
        "  /status — что настроено\n"
        "  /subscribers — все подписчики (группы и преподаватели)\n"
        "  /week_skip — пометить неделю как объявленную\n"
        "  /unsubscribe — отписаться\n\n"
        "В беседе команды применяются к текущей беседе. "
        "В ЛС — к беседе, выбранной через /use или указанной как peer=N."
    )
    await _reply(message, text)


async def cmd_where(message: Message, conn) -> None:
    peer_id, entity, _ = await _resolve_entity_or_reply(message, conn)
    if entity is None:
        return
    if entity["type"] == "group":
        await message.answer(f"📍 `peer={peer_id}`\nГруппа: {entity['division']} / {entity['group_name']}")
    else:
        await message.answer(f"📍 `peer={peer_id}`\nПреподаватель: {entity['teacher_name']}")


async def _send_day(message: Message, conn, target: date) -> None:
    peer_id, entity, _ = await _resolve_entity_or_reply(message, conn)
    if entity is None:
        return

    base = _load_base(conn)
    if base is None:
        await message.answer("⚠️ Не удалось получить справочники.")
        return

    try:
        if entity["type"] == "group":
            lessons = schedule_api.get_group_lessons(base, entity["group_uuid"], target)
            home = schedule_api.home_territory(base, entity["group_uuid"])
            unique_id = entity["group_uuid"]
        else:
            lessons = schedule_api.get_teacher_lessons(base, entity["teacher_uuid"], target)
            home = ""
            unique_id = entity["teacher_uuid"]
    except schedule_api.ScheduleAPIError as e:
        logger.error("Ошибка API: %s", e)
        await message.answer("⚠️ Не удалось получить расписание. Попробуйте позже.")
        return

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
    await send_schedule_message(message.ctx_api, peer_id, msg, conn=conn, group_uuid=unique_id)


async def _send_week(message: Message, conn, monday: date) -> None:
    peer_id, entity, _ = await _resolve_entity_or_reply(message, conn)
    if entity is None:
        return

    base = _load_base(conn)
    if base is None:
        await message.answer("⚠️ Не удалось получить справочники.")
        return

    if entity["type"] == "group":
        week = schedule_api.get_week_lessons(base, entity["group_uuid"], monday)
        home = schedule_api.home_territory(base, entity["group_uuid"])
        unique_id = entity["group_uuid"]
    else:
        week = schedule_api.get_teacher_week_lessons(base, entity["teacher_uuid"], monday)
        home = ""
        unique_id = entity["teacher_uuid"]

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
    await send_schedule_message(message.ctx_api, peer_id, msg, conn=conn, group_uuid=unique_id)


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

    peer_id, entity, _ = await _resolve_entity_or_reply(message, conn)

    lines = ["📊 Статус:"]

    if entity is None:
        lines.append("  Сущность: не определена или не зарегистрирована")
    else:
        base_age = db.base_info_age_hours(conn)
        lines.append(f"  peer_id: {peer_id}")
        if entity["type"] == "group":
            lines.append(f"  Группа: {entity['division']} / {entity['group_name']}")
            curator = entity.get("curator_id")
            lines.append(f"  Куратор: {curator if curator else '—'}")
        else:
            lines.append(f"  Преподаватель: {entity['teacher_name']}")
        lines.append(f"  Активна: {'да' if entity['is_active'] else 'нет'}")

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
    if not await _require_admin(message):
        return

    peer_id, entity, _ = await _resolve_entity_or_reply(message, conn)
    if entity is None:
        return

    if entity["type"] == "group":
        db.remove_group(conn, peer_id)
        await message.answer(f"✅ Отписка выполнена для `peer={peer_id}`.\nЧтобы снова подписаться: /group СП-X yyy")
    else:
        db.remove_teacher(conn, peer_id)
        await message.answer(
            f"✅ Отписка выполнена для `peer={peer_id}`.\nЧтобы снова подписаться: /teacher Фамилия Имя Отчество"
        )


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

    peer_id, entity, _ = await _resolve_entity_or_reply(message, conn)
    if entity is None:
        return

    await message.answer(
        f"🔔 Тестовое уведомление. Если вы его видите — бот работает.\n"
        f"Подписка: {entity['group_name'] if entity['type'] == 'group' else entity['teacher_name']}"
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
    teachers = db.get_all_teachers(conn, only_active=False)

    if not groups and not teachers:
        await message.answer("⚠️ Нет зарегистрированных бесед.")
        return

    lines = []
    if groups:
        lines.append(f"👥 Группы ({len(groups)}):")
        for g in groups:
            status = "✅" if g["is_active"] else "⏸"
            curator = g.get("curator_id")
            curator_str = f", куратор {curator}" if curator else ""
            lines.append(f"  {status} `peer={g['peer_id']}` — {g['division']} / {g['group_name']}{curator_str}")

    if teachers:
        lines.append(f"👨‍🏫 Преподаватели ({len(teachers)}):")
        for t in teachers:
            status = "✅" if t["is_active"] else "⏸"
            lines.append(f"  {status} `peer={t['peer_id']}` — {t['teacher_name']}")

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

    entity = db.get_entity(conn, peer_id)
    if entity is None:
        await message.answer(f"⚠️ Беседа `peer={peer_id}` не зарегистрирована.")
        return

    unique_id = entity.get("group_uuid") or entity.get("teacher_uuid")
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

    db.set_week_announced(conn, unique_id, week_start, is_full=False)
    await message.answer(
        f"✅ Неделя с {week_start.strftime('%d.%m.%Y')} помечена "
        f"как «объявленная» для `peer={peer_id}`.\n"
        f"Бот не будет присылать полное расписание этой недели "
        f"автоматически. Если хотите увидеть его сейчас — /week."
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
    if peer_id is None:
        cleaned = cleaned.strip()
        if not cleaned.isdigit():
            await message.answer(
                "❌ Формат: /use 2000000000\n" "Число — ID беседы (можно узнать командой /whoami в этой беседе)."
            )
            return
        peer_id = int(cleaned)

    entity = db.get_entity(conn, peer_id)  # <--- ВМЕСТО db.get_group
    if entity is None:
        await message.answer(
            f"⚠️ Беседа `peer={peer_id}` не зарегистрирована.\n"
            f"Сначала админ должен добавить её: /add_group или /add_teacher"
        )
        return

    db.set_user_session(conn, message.from_id, peer_id)

    if entity["type"] == "group":
        info = f"{entity['division']} / {entity['group_name']}"
    else:
        info = f"Преподаватель: {entity['teacher_name']}"

    await message.answer(
        f"✅ Активная беседа: `peer={peer_id}`\n"
        f"   {info}\n\n"
        f"Теперь команды `/today`, `/week`, `/status` и т.д. будут "
        f"применяться к этой беседе."
    )


# ============================================================
# Multi-tenant: управление преподавателями
# ============================================================


async def cmd_teacher(message: Message, conn, args: str) -> None:
    """Регистрирует беседу как преподавателя.

    Синтаксис:
        В беседе:  /teacher Фамилия Имя Отчество
        В ЛС:      /teacher peer=2000000000 Фамилия Имя Отчество
    """
    if not await _require_admin(message):
        return

    explicit_peer, cleaned_args = parse_peer_arg(args or "")

    if explicit_peer is not None:
        peer_id = explicit_peer
    elif is_group_chat(message.peer_id):
        peer_id = message.peer_id
    else:
        await message.answer("❌ В ЛС нужно указать беседу явно:\n  /teacher peer=2000000000 Фамилия Имя Отчество")
        return

    teacher_name = cleaned_args.strip()
    if not teacher_name:
        await message.answer("❌ Формат: /teacher Фамилия Имя Отчество")
        return

    base = _load_base(conn)
    if base is None:
        await message.answer("⚠️ Не удалось получить справочники. Попробуйте позже.")
        return

    found = schedule_api.find_teacher(base, teacher_name)
    if found is None:
        await message.answer(f"❌ Преподаватель «{teacher_name}» не найден в базе колледжа.")
        return

    if not found.get("divisions"):
        await message.answer(f"❌ У преподавателя «{teacher_name}» нет привязанных подразделений (он не ведёт пары).")
        return

    db.add_teacher(conn, peer_id=peer_id, teacher_uuid=found["id"], teacher_name=found["name"])

    await message.answer(
        f"✅ Преподаватель зарегистрирован:\n"
        f"   peer_id: {peer_id}\n"
        f"   ФИО: {found['name']}\n\n"
        f"Бот будет присылать расписание и уведомления в эту беседу."
    )


async def cmd_add_teacher(message: Message, conn, args: str) -> None:
    """Регистрирует беседу как преподавателя.

    Синтаксис:
        В беседе:  /add_teacher Фамилия Имя Отчество
        В ЛС:      /add_teacher peer=2000000000 Фамилия Имя Отчество
    """
    if not await _require_admin(message):
        return

    explicit_peer, cleaned_args = parse_peer_arg(args or "")

    if explicit_peer is not None:
        peer_id = explicit_peer
    elif is_group_chat(message.peer_id):
        peer_id = message.peer_id
    else:
        await message.answer("❌ В ЛС нужно указать беседу явно:\n  /add_teacher peer=2000000000 Фамилия Имя Отчество")
        return

    teacher_name = cleaned_args.strip()
    if not teacher_name:
        await message.answer(
            "❌ Формат: /add_teacher Фамилия Имя Отчество\nВ ЛС: /add_teacher peer=2000000000 Фамилия Имя Отчество"
        )
        return

    base = _load_base(conn)
    if base is None:
        await message.answer("⚠️ Не удалось получить справочники. Попробуйте позже.")
        return

    found = schedule_api.find_teacher(base, teacher_name)
    if found is None:
        await message.answer(f"❌ Преподаватель «{teacher_name}» не найден в базе колледжа.")
        return

    if not found.get("divisions"):
        await message.answer(f"❌ У преподавателя «{teacher_name}» нет привязанных подразделений (он не ведёт пары).")
        return

    db.add_teacher(conn, peer_id=peer_id, teacher_uuid=found["id"], teacher_name=found["name"])

    await message.answer(
        f"✅ Преподаватель зарегистрирован:\n"
        f"   peer_id: {peer_id}\n"
        f"   ФИО: {found['name']}\n\n"
        f"Бот будет присылать расписание и уведомления в эту беседу."
    )


async def cmd_remove_teacher(message: Message, conn, args: str) -> None:
    """Удаляет регистрацию преподавателя."""
    if not await _require_admin(message):
        return

    explicit_peer, _ = parse_peer_arg(args or "")

    if explicit_peer is not None:
        peer_id = explicit_peer
    elif is_group_chat(message.peer_id):
        peer_id = message.peer_id
    else:
        await message.answer("❌ В ЛС нужно указать беседу:\n  /remove_teacher peer=2000000000")
        return

    teacher = db.get_teacher(conn, peer_id)
    if teacher is None:
        await message.answer(f"⚠️ Беседа `peer={peer_id}` не зарегистрирована как преподаватель.")
        return

    db.remove_teacher(conn, peer_id)
    await message.answer(f"✅ Преподаватель `peer={peer_id}` ({teacher['teacher_name']}) удалён из бота.")


async def cmd_teachers(message: Message, conn) -> None:
    """Список всех зарегистрированных преподавателей. Только для админов."""
    if not await _require_admin(message):
        return

    teachers = db.get_all_teachers(conn, only_active=False)
    if not teachers:
        await message.answer("📋 Преподавателей нет.")
        return

    active_session = db.get_user_session(conn, message.from_id) if is_personal_chat(message.peer_id) else None

    lines = [f"👨‍🏫 Преподаватели ({len(teachers)}):"]
    for t in teachers:
        status = "✅" if t["is_active"] else "⏸"
        marker = " 👈" if active_session == t["peer_id"] else ""
        lines.append(f"  {status} `peer={t['peer_id']}` — {t['teacher_name']}{marker}")

    if is_personal_chat(message.peer_id):
        lines.append("")
        lines.append("Выбрать преподавателя: /use <peer_id>")

    await message.answer("\n".join(lines))


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

    Возвращает (user_id, "") при успехе, (None, "текст ошибки") при неудаче.

    Сначала пробуем `utils.resolve_screen_name` (надёжнее для username),
    потом fallback на `users.get` — на случай, если первый метод
    недоступен в каком-то окружении.
    """
    arg = (arg or "").strip()

    # 0. VK подставляет упоминание как [id123|Имя]. Если видим такое —
    #    сразу вытаскиваем ID, никаких запросов к API не надо.
    m = re.search(r"\[id(\d+)\|[^\]]+\]", arg)
    if m:
        return int(m.group(1)), ""

    # Убираем @ у username
    arg = arg.lstrip("@")
    if not arg:
        return None, "Пустой аргумент."

    # 1. Если это число — сразу возвращаем
    if arg.isdigit():
        return int(arg), ""

    uid: int | None = None

    # 2. resolve_screen_name — надёжнее для username
    try:
        resolved = await ctx_api.utils.resolve_screen_name(screen_name=arg)
        logger.info("resolve_screen_name(%r) → %r", arg, resolved)
        if resolved is not None:
            obj_id = getattr(resolved, "object_id", None)
            if obj_id is None and isinstance(resolved, dict):
                obj_id = resolved.get("object_id")
            obj_type = getattr(resolved, "type", None)
            # Только если это user (не группа)
            type_value = getattr(obj_type, "value", obj_type)
            if obj_id and (type_value == "user" or type_value is None):
                uid = int(obj_id)
    except Exception as e:
        logger.warning("resolve_screen_name(%r) упал: %s", arg, e)

    # 3. Fallback — users.get
    if uid is None:
        try:
            users = await ctx_api.users.get(user_ids=[arg])
            logger.info("users.get([%r]) → %d шт.", arg, len(users or []))
            if users:
                user = users[0]
                uid_value = getattr(user, "id", None)
                if uid_value is None and isinstance(user, dict):
                    uid_value = user.get("id")
                if uid_value:
                    uid = int(uid_value)
        except Exception as e:
            logger.warning("users.get([%r]) упал: %s", arg, e)

    if uid is None:
        return None, f"Пользователь «{arg}» не найден."

    return uid, ""


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
            "❌ Не удалось определить беседу.\n"
            "В ЛС: укажите явно — `/set_curator 123456 peer=2000000000`\n"
            "или сначала выберите через `/use 2000000000`."
        )
        return

    group = db.get_group(conn, peer_id)
    if group is None:
        # Проверяем, может это преподаватель?
        teacher = db.get_teacher(conn, peer_id)
        if teacher is not None:
            await message.answer("👤 У преподавателя нет куратора.")
            return
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
    peer_id, entity, _ = await _resolve_entity_or_reply(message, conn)
    if entity is None:
        return

    if entity["type"] == "teacher":
        await message.answer(f"👤 У преподавателя нет куратора.")
        return

    group = entity  # для совместимости с нижним кодом
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
    peer_id, group, cleaned_args = await _resolve_entity_or_reply(message, conn, args or "")
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
