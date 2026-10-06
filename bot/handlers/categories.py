"""/categories — свои категории событий: посмотреть, добавить, удалить."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import categories, repo
from bot.config import Config
from bot.keyboards import CatManageCb, categories_kb, category_delete_kb

router = Router(name="categories")


class NewCategory(StatesGroup):
    name = State()


async def _overview(session: AsyncSession, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    cats = await repo.list_categories(session, user_id)
    if cats:
        lines = "\n".join(f"• {c.name}" for c in cats)
        text = (
            f"🏷 Ваши категории:\n{lines}\n\n"
            "События без категории попадают только в общие списки. "
            "Чтобы удалить категорию, нажмите на неё."
        )
    else:
        text = "🏷 Категорий пока нет. Добавьте первую:"
    return text, categories_kb([(c.id, c.name) for c in cats])


@router.message(Command("categories"))
async def cmd_categories(message: Message, session: AsyncSession, config: Config) -> None:
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    text, markup = await _overview(session, user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(CatManageCb.filter(F.action == "add"))
async def category_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(NewCategory.name)
    await callback.message.answer(
        "Как назвать новую категорию? Можно начать с эмодзи, например: «🐶 Собака». "
        "(отменить: /cancel)"
    )
    await callback.answer()


@router.message(NewCategory.name, F.text, ~F.text.startswith("/"))
async def category_name(
    message: Message, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    name = categories.clean_name(message.text)
    if not name:
        await message.answer("Напишите название категории текстом.")
        return
    await state.clear()
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    category = await repo.add_category(session, user.id, name)
    text, markup = await _overview(session, user.id)
    await message.answer(f"✅ Категория «{category.name}» добавлена\n\n{text}", reply_markup=markup)


@router.callback_query(CatManageCb.filter(F.action == "ask_delete"))
async def category_ask_delete(
    callback: CallbackQuery, callback_data: CatManageCb, session: AsyncSession, config: Config
) -> None:
    user = await repo.get_or_create_user(session, callback.from_user.id, config.default_tz)
    category = await repo.get_category(session, user.id, callback_data.category_id)
    if category is None:
        await callback.answer("Такой категории уже нет.", show_alert=True)
        return
    await callback.message.edit_text(
        f"Удалить категорию «{category.name}»? Её события останутся, но без категории.",
        reply_markup=category_delete_kb(category.id),
    )
    await callback.answer()


@router.callback_query(CatManageCb.filter(F.action.in_({"delete", "back"})))
async def category_delete(
    callback: CallbackQuery, callback_data: CatManageCb, session: AsyncSession, config: Config
) -> None:
    user = await repo.get_or_create_user(session, callback.from_user.id, config.default_tz)
    note = ""
    if callback_data.action == "delete":
        category = await repo.get_category(session, user.id, callback_data.category_id)
        if category is not None:
            note = f"🗑 Категория «{category.name}» удалена\n\n"
            await repo.delete_category(session, user.id, category.id)
    text, markup = await _overview(session, user.id)
    await callback.message.edit_text(note + text, reply_markup=markup)
    await callback.answer()
