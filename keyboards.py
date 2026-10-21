"""Сборка VK-клавиатур (Reply и Inline)."""

from __future__ import annotations

from vkbottle import Keyboard, KeyboardButtonColor, Text, Callback

# ============================================================
# Inline-клавиатуры (для бесед и /help)
# ============================================================


def get_public_inline_keyboard() -> str:
    """Кнопки для обычных пользователей."""
    kb = Keyboard(inline=True)

    # Ряд 1: 2 кнопки
    kb.add(Callback("Сегодня", payload={"cmd": "/today"}), color=KeyboardButtonColor.PRIMARY)
    kb.add(Callback("Завтра", payload={"cmd": "/tomorrow"}), color=KeyboardButtonColor.PRIMARY)

    # Ряд 2: 2 кнопки
    kb.row()
    kb.add(Callback("Неделя", payload={"cmd": "/week"}), color=KeyboardButtonColor.PRIMARY)
    kb.add(Callback("След.", payload={"cmd": "/nextweek"}), color=KeyboardButtonColor.PRIMARY)

    # Ряд 3: 1 кнопка (или 2, если влезет)
    kb.row()
    kb.add(Callback("Звонки", payload={"cmd": "/bell"}), color=KeyboardButtonColor.SECONDARY)

    return kb.get_json()


def get_admin_inline_keyboard() -> str:
    """Кнопки для админов (включают публичные + админские)."""
    kb = Keyboard(inline=True)

    # Ряд 1: 3 кнопки (публичные)
    kb.add(Callback("Сегодня", payload={"cmd": "/today"}), color=KeyboardButtonColor.PRIMARY)
    kb.add(Callback("Завтра", payload={"cmd": "/tomorrow"}), color=KeyboardButtonColor.PRIMARY)
    kb.add(Callback("Неделя", payload={"cmd": "/week"}), color=KeyboardButtonColor.PRIMARY)

    # Ряд 2: 3 кнопки
    kb.row()
    kb.add(Callback("След.", payload={"cmd": "/nextweek"}), color=KeyboardButtonColor.PRIMARY)
    kb.add(Callback("Звонки", payload={"cmd": "/bell"}), color=KeyboardButtonColor.SECONDARY)
    kb.add(Callback("Где я", payload={"cmd": "/where"}), color=KeyboardButtonColor.SECONDARY)

    # Ряд 3: 3 кнопки (админские)
    kb.row()
    kb.add(Callback("Куратор", payload={"cmd": "/curator"}), color=KeyboardButtonColor.SECONDARY)
    kb.add(Callback("Статус", payload={"cmd": "/status"}), color=KeyboardButtonColor.SECONDARY)
    kb.add(Callback("Стоп", payload={"cmd": "/unsubscribe"}), color=KeyboardButtonColor.NEGATIVE)

    return kb.get_json()


# ============================================================
# Reply-клавиатура (для ЛС)
# ============================================================


def get_pm_reply_keyboard() -> str:
    """Кнопки, которые висят внизу экрана в ЛС."""
    kb = Keyboard(inline=False, one_time=False)

    # 1-й ряд (публичные)
    kb.add(Text("Сегодня"), color=KeyboardButtonColor.PRIMARY)
    kb.add(Text("Завтра"), color=KeyboardButtonColor.PRIMARY)
    kb.add(Text("Неделя"), color=KeyboardButtonColor.PRIMARY)

    # 2-й ряд (публичные)
    kb.row()
    kb.add(Text("След. неделя"), color=KeyboardButtonColor.PRIMARY)
    kb.add(Text("Звонки"), color=KeyboardButtonColor.SECONDARY)
    kb.add(Text("Где я"), color=KeyboardButtonColor.SECONDARY)

    # 3-й ряд (админские ЛС)
    kb.row()
    kb.add(Text("Группы"), color=KeyboardButtonColor.SECONDARY)
    kb.add(Text("Преподаватели"), color=KeyboardButtonColor.SECONDARY)
    kb.add(Text("Статус"), color=KeyboardButtonColor.SECONDARY)

    # 4-й ряд (админские ЛС с аргументами)
    kb.row()
    kb.add(Text("Добавить группу"), color=KeyboardButtonColor.SECONDARY)
    kb.add(Text("Добавить препода"), color=KeyboardButtonColor.SECONDARY)

    # 5-й ряд (админские ЛС с аргументами)
    kb.row()
    kb.add(Text("Интервал"), color=KeyboardButtonColor.SECONDARY)
    kb.add(Text("Пропустить неделю"), color=KeyboardButtonColor.SECONDARY)

    return kb.get_json()
