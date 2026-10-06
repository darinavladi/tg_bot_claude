import calendar
from datetime import date, timedelta

from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

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


# ---------- Главное меню (кнопки под полем ввода) ----------

MENU = {
    "add": "➕ Добавить",
    "today": "📅 Сегодня",
    "week": "🗓 Неделя",
    "list": "📋 Все события",
    "done": "✅ Завершить",
    "categories": "🏷 Категории",
    "settings": "⚙️ Настройки",
    "help": "❓ Помощь",
}


def main_menu():
    kb = ReplyKeyboardBuilder()
    for label in MENU.values():
        kb.button(text=label)
    kb.adjust(2)
    return kb.as_markup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Например: завтра в 9 врач",
    )


# ---------- Выбор даты и времени кнопками ----------

_MONTHS_NOM = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]  # fmt: skip


class CalCb(CallbackData, prefix="cal"):
    action: str  # day | nav | noop
    y: int = 0
    m: int = 0
    d: int = 0


class TimeCb(CallbackData, prefix="tm"):
    kind: str  # h — час, m — минуты, back — к календарю
    v: int = 0


class EndCb(CallbackData, prefix="end"):
    minutes: int  # длительность; 0 — без окончания; -1 — назад к выбору времени


def calendar_kb(year: int, month: int, today: date):
    """Календарь на месяц: прошедшие дни недоступны, сегодня отмечено точкой."""
    kb = InlineKeyboardBuilder()
    noop = CalCb(action="noop")
    kb.button(
        text="Сегодня", callback_data=CalCb(action="day", y=today.year, m=today.month, d=today.day)
    )
    tomorrow = today + timedelta(days=1)
    kb.button(
        text="Завтра",
        callback_data=CalCb(action="day", y=tomorrow.year, m=tomorrow.month, d=tomorrow.day),
    )
    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    can_go_back = (year, month) > (today.year, today.month)
    kb.button(
        text="◀️" if can_go_back else " ",
        callback_data=CalCb(action="nav", y=prev_y, m=prev_m) if can_go_back else noop,
    )
    kb.button(text=f"{_MONTHS_NOM[month - 1]} {year}", callback_data=noop)
    kb.button(text="▶️", callback_data=CalCb(action="nav", y=next_y, m=next_m))
    for name in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"):
        kb.button(text=name, callback_data=noop)
    weeks = calendar.Calendar().monthdayscalendar(year, month)
    for week in weeks:
        for day in week:
            if day == 0:
                kb.button(text=" ", callback_data=noop)
                continue
            d = date(year, month, day)
            if d < today:
                kb.button(text="·", callback_data=noop)
            else:
                text = f"•{day}•" if d == today else str(day)
                kb.button(text=text, callback_data=CalCb(action="day", y=year, m=month, d=day))
    kb.adjust(2, 3, 7, *([7] * len(weeks)))
    return kb.as_markup()


def hours_kb(min_hour: int = 0):
    """Час начала. Для сегодняшнего дня прошедшие часы не показываем."""
    kb = InlineKeyboardBuilder()
    hours = [h for h in range(6, 24) if h >= min_hour] or list(range(min_hour, 24))
    for hour in hours:
        kb.button(text=f"{hour:02d}:00", callback_data=TimeCb(kind="h", v=hour))
    kb.button(text="◀️ Другой день", callback_data=TimeCb(kind="back"))
    kb.adjust(*([6] * (len(hours) // 6)), *([len(hours) % 6] if len(hours) % 6 else []), 1)
    return kb.as_markup()


def minutes_kb(hour: int):
    kb = InlineKeyboardBuilder()
    for minute in (0, 15, 30, 45):
        kb.button(text=f"{hour:02d}:{minute:02d}", callback_data=TimeCb(kind="m", v=minute))
    kb.button(text="◀️ Другой час", callback_data=TimeCb(kind="hours"))
    kb.adjust(4, 1)
    return kb.as_markup()


END_OPTIONS = [(0, "Без окончания"), (30, "30 мин"), (60, "1 час"), (90, "1,5 часа"),
               (120, "2 часа"), (180, "3 часа")]  # fmt: skip


def end_kb():
    kb = InlineKeyboardBuilder()
    for minutes, label in END_OPTIONS:
        kb.button(text=label, callback_data=EndCb(minutes=minutes))
    kb.button(text="◀️ Другое время", callback_data=EndCb(minutes=-1))
    kb.adjust(1, 3, 2, 1)
    return kb.as_markup()
