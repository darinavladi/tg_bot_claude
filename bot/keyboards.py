from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.categories import NONE_LABEL
from bot.formatting import format_remind

REMIND_OPTIONS = [0, 15, 60, 1440]
# Готовые периоды повтора; «custom» — пользователь пишет свой («раз в 3 дня»)
REPEAT_PRESETS = [
    ("none", "Не повторять"), ("1d", "Каждый день"), ("wd", "По будням"),
    ("1w", "Каждую неделю"), ("2w", "Раз в 2 недели"), ("1m", "Каждый месяц"),
    ("custom", "✏️ Свой период"),
]  # fmt: skip


# Кнопки из старых версий: их обработчик только говорит, что кнопка устарела
class ConfirmCb(CallbackData, prefix="confirm"):
    action: str


class RemindCb(CallbackData, prefix="remind"):
    minutes: int


# ---------- Добавление события ----------


class AddRepeatCb(CallbackData, prefix="arep"):
    value: str  # none | код повтора | custom


class AddCatCb(CallbackData, prefix="acat"):
    value: int  # id категории; 0 — без категории; -1 — создать новую


def add_repeat_kb():
    kb = InlineKeyboardBuilder()
    for value, label in REPEAT_PRESETS:
        kb.button(text=label, callback_data=AddRepeatCb(value=value))
    kb.adjust(1, 2, 2, 1, 1)
    return kb.as_markup()


def add_category_kb(categories: list[tuple[int, str]]):
    kb = InlineKeyboardBuilder()
    for category_id, name in categories:
        kb.button(text=name, callback_data=AddCatCb(value=category_id))
    kb.button(text=NONE_LABEL, callback_data=AddCatCb(value=0))
    kb.button(text="➕ Новая категория", callback_data=AddCatCb(value=-1))
    kb.adjust(*([2] * (len(categories) // 2)), *([1] * (len(categories) % 2)), 1, 1)
    return kb.as_markup()


# ---------- Напоминание и завершение ----------


class ReminderCb(CallbackData, prefix="rem"):
    action: str  # snooze (done — у старых напоминаний)
    event_id: int
    minutes: int = 0


SNOOZE_OPTIONS = [(10, "10 мин"), (60, "1 час")]


def reminder_kb(event_id: int):
    kb = InlineKeyboardBuilder()
    for minutes, label in SNOOZE_OPTIONS:
        kb.button(
            text=f"💤 Отложить на {label}",
            callback_data=ReminderCb(action="snooze", event_id=event_id, minutes=minutes),
        )
    kb.adjust(len(SNOOZE_OPTIONS))
    return kb.as_markup()


class DoneCb(CallbackData, prefix="done"):
    event_id: int


def done_kb(buttons: list[tuple[str, int]]):
    kb = InlineKeyboardBuilder()
    for text, event_id in buttons:
        kb.button(text=f"✅ {text}"[:60], callback_data=DoneCb(event_id=event_id))
    kb.adjust(1)
    return kb.as_markup()


# ---------- Списки и карточка события ----------


class ListCb(CallbackData, prefix="lst"):
    kind: str  # today | week | all
    page: int
    cat: int = 0  # фильтр: 0 — все, -1 — без категории, иначе id категории
    pick: int = 0  # 1 — показать выбор фильтра


class EventCb(CallbackData, prefix="ev"):
    # show | title | time | remind | repeat | category | delete | delete_yes | delete_no
    action: str
    event_id: int


class RepeatCb(CallbackData, prefix="rep"):
    event_id: int
    value: str  # none | код повтора | custom


class CategoryCb(CallbackData, prefix="cat"):
    event_id: int
    value: int  # id категории; 0 — без категории; -1 — создать новую


class EditRemindCb(CallbackData, prefix="erem"):
    event_id: int
    minutes: int


def list_kb(
    buttons: list[tuple[str, int]], kind: str, page: int, pages: int,
    has_filter: bool = False, cat: int = 0, cat_label: str = "Все",
):  # fmt: skip
    """Кнопка на каждое событие страницы, ◀️ ▶️ для листания и кнопка фильтра."""
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
    if has_filter or cat:
        kb.button(
            text=f"🏷 Категория: {cat_label}"[:60],
            callback_data=ListCb(kind=kind, page=0, cat=cat, pick=1),
        )
        rows.append(1)
    kb.adjust(*rows)
    return kb.as_markup()


def list_filter_kb(kind: str, options: list[tuple[int, str]], cat: int):
    """Выбор категории для списка: options — пары (значение фильтра, подпись)."""
    kb = InlineKeyboardBuilder()
    for value, label in [(0, "Все события"), *options]:
        mark = "✓ " if value == cat else ""
        kb.button(text=f"{mark}{label}"[:60], callback_data=ListCb(kind=kind, page=0, cat=value))
    kb.adjust(1)
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
    for value, label in REPEAT_PRESETS:
        kb.button(text=label, callback_data=RepeatCb(event_id=event_id, value=value))
    kb.adjust(1, 2, 2, 1, 1)
    return kb.as_markup()


def category_kb(event_id: int, categories: list[tuple[int, str]]):
    kb = InlineKeyboardBuilder()
    for category_id, name in categories:
        kb.button(text=name, callback_data=CategoryCb(event_id=event_id, value=category_id))
    kb.button(text=NONE_LABEL, callback_data=CategoryCb(event_id=event_id, value=0))
    kb.button(text="➕ Новая категория", callback_data=CategoryCb(event_id=event_id, value=-1))
    kb.adjust(*([2] * (len(categories) // 2)), *([1] * (len(categories) % 2)), 1, 1)
    return kb.as_markup()


# ---------- /categories ----------


class CatManageCb(CallbackData, prefix="cm"):
    action: str  # add | ask_delete | delete | back
    category_id: int = 0


def categories_kb(categories: list[tuple[int, str]]):
    kb = InlineKeyboardBuilder()
    for category_id, name in categories:
        kb.button(
            text=f"🗑 {name}"[:60],
            callback_data=CatManageCb(action="ask_delete", category_id=category_id),
        )
    kb.button(text="➕ Добавить категорию", callback_data=CatManageCb(action="add"))
    kb.adjust(1)
    return kb.as_markup()


def category_delete_kb(category_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(
        text="🗑 Да, удалить", callback_data=CatManageCb(action="delete", category_id=category_id)
    )
    kb.button(text="Нет", callback_data=CatManageCb(action="back"))
    return kb.as_markup()
