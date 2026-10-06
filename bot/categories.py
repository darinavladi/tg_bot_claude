"""Как показывать категории: эмодзи впереди названия события или «· Название» после него."""

import unicodedata

NONE_LABEL = "📂 Без категории"


def emoji_of(name: str) -> str | None:
    """Эмодзи в начале названия категории («💼 Работа» → «💼»), если оно есть."""
    head = name.split(maxsplit=1)[0] if name.strip() else ""
    if head and not any(unicodedata.category(ch).startswith(("L", "N")) for ch in head):
        return head
    return None


def with_category(title: str, name: str | None) -> str:
    """«💼 Отчёт» для категории с эмодзи, «Отчёт · Собака» для категории без него."""
    if not name:
        return title
    mark = emoji_of(name)
    return f"{mark} {title}" if mark else f"{title} · {name}"


def clean_name(text: str) -> str:
    """Название новой категории: без лишних пробелов, первая буква заглавная, до 40 символов."""
    name = " ".join(text.split())[:40]
    mark = emoji_of(name)
    if mark and len(name) > len(mark):
        rest = name[len(mark) :].strip()
        return f"{mark} {rest[:1].upper()}{rest[1:]}"
    return name[:1].upper() + name[1:]


def match_hashtag(tag: str, names: dict[int, str]) -> int | None:
    """Категория по хэштегу «#работа» или «#раб»: сравниваем с названием без эмодзи."""
    tag = tag.lower().replace("ё", "е")
    if len(tag) < 3:
        return None
    for category_id, name in names.items():
        mark = emoji_of(name)
        word = (name[len(mark) :] if mark else name).strip().lower().replace("ё", "е")
        word = word.replace(" ", "_")
        # «#работе» тоже подходит к «Работа»: сравниваем без последней буквы
        stem = word[: max(3, len(word) - 1)]
        if word and (word.startswith(tag) or tag.startswith(stem)):
            return category_id
    return None
