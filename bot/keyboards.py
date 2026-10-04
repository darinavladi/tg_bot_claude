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
