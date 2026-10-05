"""Категории событий: метка с эмодзи, хэштег в тексте и угадывание по словам."""

import re

# ключ в базе → (эмодзи, название)
CATEGORIES = {
    "work": ("💼", "Работа"),
    "home": ("🏠", "Дом"),
    "health": ("🩺", "Здоровье"),
    "study": ("📚", "Учёба"),
    "people": ("👥", "Встречи"),
    "fun": ("🎉", "Отдых"),
}

# Хэштеги в тексте: «#работа», «#дом»… (начало слова, чтобы подошло и «#работе»)
_TAGS = {
    "работ": "work",
    "дом": "home",
    "здоров": "health",
    "врач": "health",
    "учёб": "study",
    "учеб": "study",
    "встреч": "people",
    "друз": "people",
    "семь": "people",
    "отдых": "fun",
}
_HASHTAG = re.compile(r"(?<!\w)#(\w+)")

# Если хэштега нет, угадываем по началу слов в названии
_GUESS = {
    "health": (
        "врач", "стоматолог", "зубн", "терапевт", "анализ", "больниц", "поликлиник",
        "таблет", "лекарств", "массаж", "тренировк", "спортзал", "зарядк", "бассейн",
    ),
    "work": ("работ", "планёрк", "планерк", "созвон", "совещан", "дедлайн", "отчёт", "отчет"),
    "study": ("урок", "лекци", "экзамен", "зачёт", "зачет", "семинар", "курс", "домашк"),
    "home": ("уборк", "стирк", "квартплат", "коммунал", "продукт", "магазин", "ремонт"),
    "people": ("встреч", "день рождени", "др ", "мам", "пап", "бабушк", "подруг", "друз"),
    "fun": ("кино", "театр", "концерт", "выставк", "музей", "отпуск", "прогулк", "вечеринк"),
}  # fmt: skip


def label(category: str | None) -> str:
    """«💼 Работа»; для события без категории — «Без категории»."""
    if category not in CATEGORIES:
        return "Без категории"
    emoji, name = CATEGORIES[category]
    return f"{emoji} {name}"


def emoji(category: str | None) -> str:
    return CATEGORIES[category][0] if category in CATEGORIES else ""


def with_emoji(title: str, category: str | None) -> str:
    """Название с эмодзи категории впереди: «🩺 Стоматолог»."""
    mark = emoji(category)
    return f"{mark} {title}" if mark else title


def extract_hashtag(text: str) -> tuple[str | None, str]:
    """Находит «#работа» и т.п.; возвращает категорию и текст без этого хэштега."""
    for m in _HASHTAG.finditer(text):
        tag = m[1].lower()
        for prefix, category in _TAGS.items():
            if tag.startswith(prefix):
                return category, text[: m.start()] + text[m.end() :]
    return None, text


def guess(title: str) -> str | None:
    text = f" {title.lower()} "
    for category, words in _GUESS.items():
        if any(f" {word}" in text for word in words):
            return category
    return None


def detect(text: str) -> tuple[str | None, str]:
    """Категория по хэштегу, а без него — по словам. Возвращает (категория, текст)."""
    category, text = extract_hashtag(text)
    return category or guess(text), text
