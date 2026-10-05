from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.categories import CATEGORIES
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
    cat: str = ""  # фильтр по категории ("" — все)


class EventCb(CallbackData, prefix="ev"):
    # show | title | time | remind | repeat | category | delete | delete_yes | delete_no
    action: str
    event_id: int


class RepeatCb(CallbackData, prefix="rep"):
    event_id: int
    value: str  # none | daily | weekdays | weekly | monthly


class CategoryCb(CallbackData, prefix="cat"):
    event_id: int
    value: str  # none | ключ категории


class EditRemindCb(CallbackData, prefix="erem"):
    event_id: int
    minutes: int


def list_kb(
    buttons: list[tuple[str, int]], kind: str, page: int, pages: int,
    cats: list[str] = (), cat: str = "",
):  # fmt: skip
    """Кнопка на каждое событие страницы, ◀️ ▶️ для листания и фильтр по категориям.

    cats — категории, которые есть в списке; фильтр показываем, если их хотя бы две
    (или если фильтр уже включён, чтобы его можно было снять).
    """
    kb = InlineKeyboardBuilder()
    for text, event_id in buttons:
        kb.button(text=text, callback_data=EventCb(action="show", event_id=event_id))
    rows = [1] * len(buttons)
    nav = 0
    if page > 0:
        kb.button(text="◀️", callback_data=ListCb(kind=kind, page=page - 1, cat=cat))
        nav += 1
    if page < pages - 1:
        kb.button(text="▶️", callback_data=ListCb(kind=kind, page=page + 1, cat=cat))
        nav += 1
    if nav:
        rows.append(nav)
    if len(cats) > 1 or cat:
        kb.button(text="✓ Все" if not cat else "Все", callback_data=ListCb(kind=kind, page=0))
        for key in cats:
            mark = CATEGORIES[key][0]
            kb.button(
                text=f"✓ {mark}" if key == cat else mark,
                callback_data=ListCb(kind=kind, page=0, cat=key),
            )
        rows.append(len(cats) + 1)
    kb.adjust(*rows)
    return kb.as_markup()


def event_kb(event_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Название", callback_data=EventCb(action="title", event_id=event_id))
    kb.button(text="🕒 Время", callback_data=EventCb(action="time", event_id=event_id))
    kb.button(text="⏰ Напоминание", callback_data=EventCb(action="remind", event_id=event_id))
    kb.button(text="🔁 Повтор", callback_data=EventCb(action="repeat", event_id=event_id))
    kb.button(text="🏷 Категория", callback_data=EventCb(action="category", event_id=event_id))
    kb.button(text="🗑 Удалить", callback_data=EventCb(action="delete", event_id=event_id))
    kb.adjust(2, 2, 2)
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


# ---------- Настройки ----------

TIMEZONES = [
    ("Калининград (UTC+2)", "Europe/Kaliningrad"),
    ("Москва (UTC+3)", "Europe/Moscow"),
    ("Самара (UTC+4)", "Europe/Samara"),
    ("Екатеринбург (UTC+5)", "Asia/Yekaterinburg"),
    ("Омск (UTC+6)", "Asia/Omsk"),
    ("Новосибирск (UTC+7)", "Asia/Novosibirsk"),
    ("Красноярск (UTC+7)", "Asia/Krasnoyarsk"),
    ("Иркутск (UTC+8)", "Asia/Irkutsk"),
    ("Якутск (UTC+9)", "Asia/Yakutsk"),
    ("Владивосток (UTC+10)", "Asia/Vladivostok"),
    ("Магадан (UTC+11)", "Asia/Magadan"),
    ("Камчатка (UTC+12)", "Asia/Kamchatka"),
]
SUMMARY_TIMES = ["07:00", "08:00", "09:00", "10:00"]


class SettingsCb(CallbackData, prefix="set"):
    action: str  # menu | tz | tz_other | summary | summary_custom | remind
    value: str = ""


def settings_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🌍 Часовой пояс", callback_data=SettingsCb(action="menu", value="tz"))
    kb.button(text="☀️ Утренняя сводка", callback_data=SettingsCb(action="menu", value="summary"))
    kb.button(
        text="⏰ Напоминание по умолчанию", callback_data=SettingsCb(action="menu", value="remind")
    )
    kb.adjust(1)
    return kb.as_markup()


def timezone_kb():
    kb = InlineKeyboardBuilder()
    for label, tz in TIMEZONES:
        kb.button(text=label, callback_data=SettingsCb(action="tz", value=tz))
    kb.button(text="✏️ Другой", callback_data=SettingsCb(action="tz_other"))
    kb.adjust(2)
    return kb.as_markup()


def summary_kb():
    kb = InlineKeyboardBuilder()
    for t in SUMMARY_TIMES:
        # «:» — разделитель в callback_data, поэтому передаём время как «07.00»
        kb.button(text=t, callback_data=SettingsCb(action="summary", value=t.replace(":", ".")))
    kb.button(text="✏️ Своё время", callback_data=SettingsCb(action="summary_custom"))
    kb.button(text="🚫 Выключить", callback_data=SettingsCb(action="summary", value="off"))
    kb.adjust(4, 2)
    return kb.as_markup()


def default_remind_kb():
    kb = InlineKeyboardBuilder()
    for minutes in REMIND_OPTIONS:
        kb.button(
            text=format_remind(minutes).capitalize(),
            callback_data=SettingsCb(action="remind", value=str(minutes)),
        )
    kb.adjust(2)
    return kb.as_markup()


def repeat_kb(event_id: int):
    kb = InlineKeyboardBuilder()
    for value, label in [
        ("none", "Не повторять"), ("daily", "Каждый день"), ("weekdays", "По будням"),
        ("weekly", "Каждую неделю"), ("monthly", "Каждый месяц"),
    ]:  # fmt: skip
        kb.button(text=label, callback_data=RepeatCb(event_id=event_id, value=value))
    kb.adjust(1, 2, 2)
    return kb.as_markup()


def category_kb(event_id: int):
    kb = InlineKeyboardBuilder()
    for key, (mark, name) in CATEGORIES.items():
        kb.button(text=f"{mark} {name}", callback_data=CategoryCb(event_id=event_id, value=key))
    kb.button(text="Без категории", callback_data=CategoryCb(event_id=event_id, value="none"))
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()
