"""Telegram-бот-каталог косметики.

Товары берутся из products.json — файл перечитывается при каждом запросе,
поэтому правки видны сразу, без перезапуска бота.
"""

import asyncio
import html
import logging
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

import admin
import cloud
import stats
from catalog import find_product, load_catalog, photo_input, product_categories, product_text, save_catalog

PER_PAGE = 6  # сколько товаров показывать на одной странице категории
CAPTION_LIMIT = 1024  # ограничение Telegram на подпись к фото

dp = Dispatcher()
dp.include_router(admin.router)


# ---------- callback-данные кнопок ----------

class MenuCb(CallbackData, prefix="menu"):
    action: str  # "home" или "about"


class CatCb(CallbackData, prefix="cat"):
    id: str
    page: int = 0


class ProdCb(CallbackData, prefix="prod"):
    id: str
    cat: str  # категория и страница, на которые вернёт кнопка «Назад»
    page: int = 0


# ---------- клавиатуры ----------

def home_kb(catalog: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for cat in catalog["categories"]:
        kb.button(text=cat["title"], callback_data=CatCb(id=cat["id"]))
    kb.button(text="ℹ️ О магазине", callback_data=MenuCb(action="about"))
    kb.adjust(2)
    return kb.as_markup()


def category_kb(catalog: dict, cat_id: str, page: int) -> InlineKeyboardMarkup:
    items = [p for p in catalog["products"] if cat_id in product_categories(p)]
    pages = max(1, -(-len(items) // PER_PAGE))
    page = min(max(page, 0), pages - 1)

    kb = InlineKeyboardBuilder()
    for p in items[page * PER_PAGE:(page + 1) * PER_PAGE]:
        kb.button(text=f"{p['name']} — {p['price']}", callback_data=ProdCb(id=p["id"], cat=cat_id, page=page))
    kb.adjust(1)

    if pages > 1:
        nav = InlineKeyboardBuilder()
        if page > 0:
            nav.button(text="◀️", callback_data=CatCb(id=cat_id, page=page - 1))
        nav.button(text=f"{page + 1}/{pages}", callback_data=CatCb(id=cat_id, page=page))
        if page < pages - 1:
            nav.button(text="▶️", callback_data=CatCb(id=cat_id, page=page + 1))
        kb.attach(nav)

    kb.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data=MenuCb(action="home").pack()))
    return kb.as_markup()


def product_kb(product: dict, cat_id: str, page: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for link in product.get("buy_links", []):
        kb.row(InlineKeyboardButton(text=link["title"], url=link["url"]))
    kb.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=CatCb(id=cat_id, page=page).pack()),
        InlineKeyboardButton(text="🏠 Меню", callback_data=MenuCb(action="home").pack()),
    )
    return kb.as_markup()


# ---------- показ экрана ----------

async def show(
    call: CallbackQuery, text: str, kb: InlineKeyboardMarkup, photo: str | FSInputFile | None = None
) -> None:
    """Показывает новый «экран» вместо текущего сообщения.

    Сообщение с фото нельзя превратить в текстовое (и наоборот), поэтому в
    таких случаях старое сообщение удаляется и отправляется новое.
    """
    msg = call.message

    if photo and len(text) <= CAPTION_LIMIT:
        try:
            await msg.answer_photo(photo, caption=text, reply_markup=kb)
            await _safe_delete(msg)
            return
        except TelegramBadRequest as err:
            logging.warning("Не удалось отправить фото %s: %s", photo, err)

    if msg.photo or photo:
        await msg.answer(text, reply_markup=kb)
        await _safe_delete(msg)
    else:
        try:
            await msg.edit_text(text, reply_markup=kb)
        except TelegramBadRequest:
            pass  # «message is not modified» — экран и так тот же


async def _safe_delete(msg: Message) -> None:
    try:
        await msg.delete()
    except TelegramBadRequest:
        pass


# ---------- обработчики ----------

@dp.message(CommandStart())
@dp.message(Command("menu"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()  # выходим из режима ввода админки, если он был включён
    stats.track(message.from_user.id, "start")
    catalog = load_catalog()
    await message.answer(catalog["shop"]["welcome"], reply_markup=home_kb(catalog))


@dp.callback_query(MenuCb.filter(F.action == "home"))
async def on_home(call: CallbackQuery) -> None:
    catalog = load_catalog()
    await show(call, catalog["shop"]["welcome"], home_kb(catalog))
    await call.answer()


@dp.callback_query(MenuCb.filter(F.action == "about"))
async def on_about(call: CallbackQuery) -> None:
    stats.track(call.from_user.id, "about")
    catalog = load_catalog()
    shop = catalog["shop"]
    kb = InlineKeyboardBuilder()
    for link in shop.get("links", []):
        kb.button(text=link["title"], url=link["url"])
    kb.button(text="🏠 Главное меню", callback_data=MenuCb(action="home"))
    kb.adjust(1)
    await show(call, shop.get("about", ""), kb.as_markup())
    await call.answer()


@dp.callback_query(CatCb.filter())
async def on_category(call: CallbackQuery, callback_data: CatCb) -> None:
    catalog = load_catalog()
    cat = next((c for c in catalog["categories"] if c["id"] == callback_data.id), None)
    if cat is None:
        await call.answer("Категория не найдена", show_alert=True)
        return
    if callback_data.page == 0:  # листание страниц не считаем отдельным заходом
        stats.track(call.from_user.id, "category", cat["id"])
    text = f"<b>{html.escape(cat['title'])}</b>\n\nВыберите товар 👇"
    await show(call, text, category_kb(catalog, cat["id"], callback_data.page))
    await call.answer()


@dp.callback_query(ProdCb.filter())
async def on_product(call: CallbackQuery, callback_data: ProdCb) -> None:
    catalog = load_catalog()
    product = find_product(catalog, callback_data.id)
    if product is None:
        await call.answer("Этот товар больше недоступен", show_alert=True)
        return
    stats.track(call.from_user.id, "product", product["id"])
    kb = product_kb(product, callback_data.cat, callback_data.page)
    await show(call, product_text(product), kb, photo_input(product.get("photo")))
    await call.answer()


# ---------- запрет писать в чат ----------

# Подключается последним: сюда попадают сообщения, которые не обработали ни
# команды выше, ни админка. Покупатели пользуются только кнопками.
lockdown = Router()
dp.include_router(lockdown)

HINT_SECONDS = 4
_background: set[asyncio.Task] = set()


async def _delete_later(msg: Message, delay: float) -> None:
    await asyncio.sleep(delay)
    await _safe_delete(msg)


@lockdown.message(~admin.IsAdmin())
async def on_other_message(message: Message) -> None:
    if message.text and message.text.startswith("/admin"):
        logging.info("Запрос /admin без доступа от ID %s (@%s)", message.from_user.id, message.from_user.username)
    await _safe_delete(message)
    hint = await message.answer("✋ Писать в этот чат нельзя — пользуйтесь кнопками.\nОткрыть каталог: /start")
    task = asyncio.create_task(_delete_later(hint, HINT_SECONDS))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def setup_commands(bot: Bot) -> None:
    """Меню команд: покупателям — только /start, админам — ещё /admin и /cancel."""
    await bot.set_my_commands([BotCommand(command="start", description="Открыть каталог")])
    admin_commands = [
        BotCommand(command="start", description="Открыть каталог"),
        BotCommand(command="admin", description="Админ-панель"),
        BotCommand(command="cancel", description="Отменить ввод"),
    ]
    for uid in admin.admin_ids():
        try:
            await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=uid))
        except TelegramBadRequest as err:  # админ ещё ни разу не писал боту
            logging.warning("Не удалось задать команды для админа %s: %s", uid, err)


# ---------- разовые обновления каталога ----------

# Каталог живёт в Telegram (см. cloud.py), поэтому новые данные вносим при запуске.
# Каждое обновление выполняется один раз — его имя запоминается в catalog["done"],
# и последующие правки из админки не перезаписываются.
OZON_SKUS = {"scrub": "1782281650", "milk": "1782264160", "mist": "1782278736", "bronzer": "1782274675"}


async def apply_updates(bot: Bot) -> None:
    catalog = load_catalog()
    done = catalog.setdefault("done", [])
    if "ozon_skus" in done:
        return
    for p in catalog["products"]:
        if p["id"] in OZON_SKUS and not p.get("sku"):
            p["sku"] = OZON_SKUS[p["id"]]
    done.append("ozon_skus")
    save_catalog(catalog)
    await cloud.push(bot)
    logging.info("Артикулы Ozon добавлены в каталог")


async def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Не задан BOT_TOKEN. Создайте файл .env (см. .env.example).")

    logging.basicConfig(level=logging.INFO)
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await setup_commands(bot)
    await cloud.pull(bot)  # на бесплатном хостинге диск чистый — берём каталог из Telegram
    await apply_updates(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
