"""Логика отправки bell schedules в 18:00 дня перед целевой датой.

Публичный API:
    run_bell_checks(conn, bot_api, *, now=None) -> None
    process_group_bell(conn, bot_api, group, now) -> bool

Правила:
  - Проверка запускается один раз в день после 18:00.
  - Для каждой активной группы смотрим bell_schedule на `tomorrow`.
  - Если bell_schedule найден:
      * detected_at == сегодня → не отправляем (куратор только что прислал).
      * detected_at == вчера   → отправляем, если в чате было >10 сообщений
                                 с момента detected_at; иначе — тишина.
      * detected_at <= позавчера → отправляем.
      * sent_at уже стоит → не повторяем.
  - Если bell_schedule НЕ найден и `tomorrow` — праздник:
      * пингуем куратора один раз (флаг в holiday_pings).
  - Если `tomorrow` — обычный день и нет bell_schedule → ничего не делаем.
"""

from __future__ import annotations

import io
import logging
from datetime import date as date_type, datetime, time, timedelta

from PIL import Image

import database as db
import holidays
from vk_upload import upload_png_to_vk

logger = logging.getLogger(__name__)

# Время, после которого запускаем проверку
BELL_CHECK_HOUR = 18

# Порог сообщений для «вчерашнего» правила
YESTERDAY_MESSAGES_THRESHOLD = 10


# ============================================================
# Вспомогательные функции
# ============================================================


def prepare_image(image_bytes: bytes) -> tuple[bytes, str, str]:
    """Готовит картинку к загрузке в VK.

    Определяет формат по сигнатуре:
      - PNG → остаётся как есть;
      - JPEG → остаётся как есть;
      - всё остальное → конвертируется в PNG через Pillow.

    Не гоняем JPEG→PNG→JPEG, чтобы не терять вес и время.
    VK принимает JPEG напрямую при корректном Content-Type.

    Returns:
        (bytes, content_type, filename)
    """
    if not image_bytes:
        return b"", "image/png", "schedule.png"

    # PNG
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return image_bytes, "image/png", "schedule.png"

    # JPEG
    if image_bytes[:3] == b"\xff\xd8\xff":
        return image_bytes, "image/jpeg", "schedule.jpg"

    # Всё остальное — конвертируем в PNG
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png", "schedule.png"
    except Exception as e:
        logger.warning("prepare_image: не удалось определить формат (%s)", e)
        return image_bytes, "image/png", "schedule.png"


async def _get_user_first_name(bot_api, user_id: int) -> str:
    """Возвращает first_name пользователя VK. При ошибке — пустая строка."""
    try:
        users = await bot_api.users.get(user_ids=[user_id])
    except Exception as e:
        logger.warning("users.get(%s) упал: %s", user_id, e)
        return ""

    if not users:
        return ""

    u = users[0]
    # vkbottle возвращает pydantic-модель или dict — пробуем оба
    name = getattr(u, "first_name", None)
    if name:
        return name
    if isinstance(u, dict):
        return u.get("first_name", "") or ""
    return ""


# ============================================================
# Отправка и пинг
# ============================================================


async def _send_bell_schedule(conn, bot_api, peer_id: int, bs: dict) -> None:
    """Отправляет сохранённое bell schedule в чат."""
    target_date = date_type.fromisoformat(bs["target_date"])
    target_str = target_date.strftime("%d.%m")

    caption = (bs.get("caption") or "").strip()

    # Формируем текст сообщения
    if caption and caption != "расписание звонков":
        text = f"🔔 Напоминаем: расписание звонков на {target_str} (завтра):\n{caption}"
    else:
        text = f"🔔 Напоминаем: расписание звонков на {target_str} (завтра)"

    image_bytes = bs.get("image_bytes")
    if image_bytes:
        prepared, content_type, filename = prepare_image(image_bytes)
        try:
            attachment = await upload_png_to_vk(
                bot_api, prepared, peer_id=peer_id, content_type=content_type, filename=filename
            )
            await bot_api.messages.send(peer_id=peer_id, message=text, attachment=attachment, random_id=0)
        except Exception as e:
            logger.exception("Не удалось отправить bell schedule в peer=%s: %s", peer_id, e)
            # Fallback — хотя бы текст
            await bot_api.messages.send(peer_id=peer_id, message=text, random_id=0)
    else:
        # Картинки нет — только текст
        await bot_api.messages.send(peer_id=peer_id, message=text, random_id=0)

    db.mark_bell_schedule_sent(conn, peer_id, bs["target_date"])
    logger.info("Bell schedule отправлен: peer=%s target=%s", peer_id, bs["target_date"])


async def _ping_curator(
    conn, bot_api, peer_id: int, curator_id: int | None, target: date_type, holiday_name: str
) -> None:
    """Пингует куратора в чате. Один раз на дату."""
    target_str = target.strftime("%d.%m")

    if curator_id:
        first_name = await _get_user_first_name(bot_api, curator_id)
        mention_name = first_name or "куратор"
        mention = f"[id{curator_id}|{mention_name}]"
    else:
        mention = "куратор"

    text = (
        f"🔔 Завтра {target_str} — {holiday_name}. "
        f"{mention}, проверьте, пожалуйста, расписание звонков "
        f"на наличие изменений."
    )

    try:
        await bot_api.messages.send(peer_id=peer_id, message=text, random_id=0)
    except Exception as e:
        logger.exception("Не удалось отправить пинг куратора в peer=%s: %s", peer_id, e)
        return

    db.mark_holiday_pinged(conn, peer_id, target)
    logger.info("Holiday ping отправлен: peer=%s target=%s holiday=%s", peer_id, target, holiday_name)


# ============================================================
# Основная логика для одной группы
# ============================================================


async def process_group_bell(conn, bot_api, group: dict, now: datetime) -> bool:
    """Обрабатывает bell schedule для одной группы.

    Returns:
        True, если что-то отправили (bell schedule или пинг).
    """
    peer_id = group["peer_id"]
    group_name = group["group_name"]
    curator_id = group.get("curator_id")

    target = now.date() + timedelta(days=1)
    bs = db.get_bell_schedule(conn, peer_id, target)

    # --- Case A: bell schedule найден ---
    if bs is not None:
        if bs["sent_at"] is not None:
            logger.debug("bell schedule для peer=%s target=%s уже отправляли — пропуск", peer_id, target)
            return False

        try:
            detected_at = datetime.fromisoformat(bs["detected_at"])
        except (ValueError, TypeError):
            logger.warning("bell_schedule %s: некорректный detected_at=%r", target, bs.get("detected_at"))
            detected_at = now - timedelta(days=10)  # считаем «давно» → отправляем

        detected_date = detected_at.date()
        today = now.date()

        if detected_date == today:
            logger.info("bell schedule для peer=%s target=%s прислан сегодня — не отправляем", peer_id, target)
            return False

        if detected_date == today - timedelta(days=1):
            # Считаем сообщения в чате от detected_at до «сейчас»
            n_msgs = db.count_chat_messages_since(
                conn, peer_id, since_iso=bs["detected_at"], until_iso=now.isoformat(timespec="seconds")
            )
            if n_msgs <= YESTERDAY_MESSAGES_THRESHOLD:
                logger.info(
                    "bell schedule для peer=%s target=%s (вчера), " "но сообщений с тех пор только %d — не отправляем",
                    peer_id,
                    target,
                    n_msgs,
                )
                return False
            logger.info(
                "bell schedule для peer=%s target=%s (вчера, %d сообщений) — отправляем", peer_id, target, n_msgs
            )

        # Позавчера и раньше — отправляем
        await _send_bell_schedule(conn, bot_api, peer_id, bs)
        return True

    # --- Case B: bell schedule не найден ---
    is_hol, holiday_name = holidays.is_holiday(target)
    if not is_hol:
        logger.debug("bell schedule для peer=%s target=%s отсутствует, %s — не праздник", peer_id, target, target)
        return False

    if db.was_holiday_pinged(conn, peer_id, target):
        logger.debug("Holiday ping для peer=%s target=%s уже был — пропуск", peer_id, target)
        return False

    await _ping_curator(conn, bot_api, peer_id, curator_id, target, holiday_name)
    return True


# ============================================================
# Точка входа (вызывается из background_checker)
# ============================================================


async def run_bell_checks(conn, bot_api, *, now: datetime | None = None) -> None:
    """Если сейчас >= 18:00 и ещё не запускали сегодня — обрабатываем все группы."""
    if now is None:
        now = datetime.now()

    if now.time() < time(BELL_CHECK_HOUR, 0):
        return

    last = db.get_setting(conn, "bell_check_date")
    if last == now.date().isoformat():
        return  # уже делали сегодня

    groups = db.get_all_groups(conn, only_active=True)
    if not groups:
        logger.debug("bell_checks: активных групп нет")
        db.set_setting(conn, "bell_check_date", now.date().isoformat())
        return

    logger.info("bell_checks: старт для %d групп", len(groups))

    for g in groups:
        try:
            await process_group_bell(conn, bot_api, g, now)
        except Exception as e:
            logger.exception("Ошибка bell_check для группы %s: %s", g.get("group_name"), e)

    db.set_setting(conn, "bell_check_date", now.date().isoformat())
    logger.info("bell_checks: завершены")
