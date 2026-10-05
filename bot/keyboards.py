from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.formatting import format_remind

REMIND_OPTIONS = [0, 15, 60, 1440]


class ConfirmCb(CallbackData, prefix="confirm"):
    action: str  # save | cancel


class RemindCb(CallbackData, prefix="remind"):
    minutes: int


def confirm_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Сохранить", callback_data=ConfirmCb(action="save"))
    kb.button(text="❌ Отмена", callback_data=ConfirmCb(action="cancel"))
    return kb.as_markup()


def remind_kb():
    kb = InlineKeyboardBuilder()
    for minutes in REMIND_OPTIONS:
        kb.button(text=format_remind(minutes).capitalize(), callback_data=RemindCb(minutes=minutes))
    kb.adjust(2)
    return kb.as_markup()


class ReminderCb(CallbackData, prefix="rem"):
    action: str  # done | snooze
    event_id: int
    minutes: int = 0


SNOOZE_OPTIONS = [(10, "10 мин"), (60, "1 час")]


def reminder_kb(event_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Готово", callback_data=ReminderCb(action="done", event_id=event_id))
    for minutes, label in SNOOZE_OPTIONS:
        kb.button(
            text=f"💤 {label}",
            callback_data=ReminderCb(action="snooze", event_id=event_id, minutes=minutes),
        )
    kb.adjust(1, len(SNOOZE_OPTIONS))
    return kb.as_markup()


# ---------- Списки и карточка события ----------


class ListCb(CallbackData, prefix="lst"):
    kind: str  # today | week | all
    page: int


class EventCb(CallbackData, prefix="ev"):
    action: str  # show | title | time | remind | delete | delete_yes | delete_no
    event_id: int


class EditRemindCb(CallbackData, prefix="erem"):
    event_id: int
    minutes: int


def list_kb(buttons: list[tuple[str, int]], kind: str, page: int, pages: int):
    """Кнопка на каждое событие страницы и ◀️ ▶️ для листания."""
    kb = InlineKeyboardBuilder()
    for text, event_id in buttons:
        kb.button(text=text, callback_data=EventCb(action="show", event_id=event_id))
    nav = []
    if page > 0:
        kb.button(text="◀️", callback_data=ListCb(kind=kind, page=page - 1))
        nav.append(1)
    if page < pages - 1:
        kb.button(text="▶️", callback_data=ListCb(kind=kind, page=page + 1))
        nav.append(1)
    kb.adjust(*([1] * len(buttons)), len(nav) or 1)
    return kb.as_markup()


def event_kb(event_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Название", callback_data=EventCb(action="title", event_id=event_id))
    kb.button(text="🕒 Время", callback_data=EventCb(action="time", event_id=event_id))
    kb.button(text="⏰ Напоминание", callback_data=EventCb(action="remind", event_id=event_id))
    kb.button(text="🗑 Удалить", callback_data=EventCb(action="delete", event_id=event_id))
    kb.adjust(2, 2)
    return kb.as_markup()


def delete_confirm_kb(event_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Да, удалить", callback_data=EventCb(action="delete_yes", event_id=event_id))
    kb.button(text="Нет", callback_data=EventCb(action="delete_no", event_id=event_id))
    return kb.as_markup()


def edit_remind_kb(event_id: int):
    kb = InlineKeyboardBuilder()
    for minutes in REMIND_OPTIONS:
        kb.button(
            text=format_remind(minutes).capitalize(),
            callback_data=EditRemindCb(event_id=event_id, minutes=minutes),
        )
    kb.adjust(2)
    return kb.as_markup()
