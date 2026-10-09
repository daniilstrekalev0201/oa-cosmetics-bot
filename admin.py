"""Админ-панель внутри бота: команда /admin.

Доступ есть только у Telegram ID из ADMIN_IDS в файле .env.
Все изменения сразу записываются в products.json и видны покупателям.
"""

import copy
import html
import re
import uuid

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, Filter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    TelegramObject,
)

import cloud
import ozon
import stats
from catalog import (
    admin_ids,
    find_category,
    find_product,
    load_catalog,
    product_categories,
    product_text,
    save_catalog,
)

SHOP = "_shop"  # «владелец» кнопок-ссылок раздела «О магазине»

FIELDS = {
    "name": "Название",
    "price": "Цена",
    "volume": "Объём",
    "sku": "Артикул Ozon",
    "brand": "Бренд",
    "description": "Описание",
    "composition": "Состав",
    "how_to_use": "Применение",
    "where_to_buy": "Где купить",
}
REQUIRED = {"name", "price"}
TEXTS = {"welcome": "Приветствие", "about": "О магазине"}

e = html.escape


class IsAdmin(Filter):
    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and user.id in admin_ids()


class A(CallbackData, prefix="adm"):
    act: str
    id: str = ""
    arg: str = ""


class Edit(StatesGroup):
    value = State()  # что именно ждём — лежит в данных состояния (kind, id, ...)


panel = Router()
panel.message.filter(IsAdmin())
panel.callback_query.filter(IsAdmin())

router = Router()
router.include_router(panel)


# ---------- помощники ----------

def kb_rows(*rows: list[tuple[str, A]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=cb.pack()) for t, cb in row] for row in rows if row]
    )


async def show(event: Message | CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    no_preview = LinkPreviewOptions(is_disabled=True)
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=kb, link_preview_options=no_preview)
        except TelegramBadRequest as err:
            if "not modified" not in str(err):
                await event.message.answer(text, reply_markup=kb, link_preview_options=no_preview)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb, link_preview_options=no_preview)


async def ask(event: Message | CallbackQuery, state: FSMContext, prompt: str, back: A, **data) -> None:
    """Переводит админа в режим ввода и показывает подсказку с кнопкой «Отмена»."""
    await state.set_state(Edit.value)
    await state.update_data(**data)
    await show(event, prompt, kb_rows([("✖️ Отмена", back)]))


async def save(event: Message | CallbackQuery, catalog: dict) -> None:
    """Сохраняет каталог на диск и обновляет его копию в Telegram (см. cloud.py)."""
    save_catalog(catalog)
    await cloud.push(event.bot)


def new_id(existing: list[dict], prefix: str) -> str:
    ids = {x["id"] for x in existing}
    while True:
        candidate = prefix + uuid.uuid4().hex[:5]
        if candidate not in ids:
            return candidate


def move(items: list, index: int, direction: str) -> None:
    j = index - 1 if direction == "up" else index + 1
    if 0 <= j < len(items):
        items[index], items[j] = items[j], items[index]


def links_of(catalog: dict, owner: str) -> list[dict] | None:
    if owner == SHOP:
        return catalog["shop"].setdefault("links", [])
    p = find_product(catalog, owner)
    return None if p is None else p.setdefault("buy_links", [])


# ---------- экраны ----------

def home_screen(note: str = ""):
    text = f"{note}⚙️ <b>Админ-панель</b>\n\nЧто хотите изменить? Изменения сразу видны покупателям."
    return text, kb_rows(
        [("📦 Товары", A(act="prods")), ("🗂 Разделы", A(act="cats"))],
        [("💬 Приветствие", A(act="text", id="welcome")), ("ℹ️ О магазине", A(act="text", id="about"))],
        [("🔗 Кнопки в «О магазине»", A(act="links", id=SHOP))],
        [("📊 Статистика", A(act="stats"))],
        [("✖️ Закрыть", A(act="close"))],
    )


def prods_screen(catalog: dict, note: str = ""):
    products = catalog["products"]
    rows = [[(f"{p['name']} — {p['price']}", A(act="prod", id=p["id"]))] for p in products]
    rows += [[("➕ Добавить товар", A(act="newprod"))], [("⬅️ Назад", A(act="home"))]]
    text = f"{note}📦 <b>Товары</b> ({len(products)})\n\nВыберите товар, чтобы изменить его."
    return text, kb_rows(*rows)


def prod_screen(catalog: dict, pid: str, note: str = ""):
    p = find_product(catalog, pid)
    if p is None:
        return prods_screen(catalog, "⚠️ Товар не найден.\n\n")
    titles = {c["id"]: c["title"] for c in catalog["categories"]}
    cats = ", ".join(titles.get(c, c) for c in product_categories(p)) or "нет — покупатели его не видят ⚠️"
    info = (
        "\n\n———\n"
        f"🖼 Фото: {'есть' if p.get('photo') else 'нет'}\n"
        f"🗂 Разделы: {e(cats)}\n"
        f"🔗 Кнопок покупки: {len(p.get('buy_links', []))}"
    )
    fields = [(f"✏️ {label}", A(act="pfield", id=pid, arg=key)) for key, label in FIELDS.items()]
    rows = [fields[i:i + 2] for i in range(0, len(fields), 2)]
    rows += [
        [("🖼 Фото", A(act="pphoto", id=pid)), ("🗂 Разделы", A(act="pcats", id=pid))],
        [("🔗 Кнопки покупки", A(act="links", id=pid))],
        [("⬆️ Выше в списке", A(act="pmove", id=pid, arg="up")), ("⬇️ Ниже", A(act="pmove", id=pid, arg="down"))],
        [("🗑 Удалить товар", A(act="pdel", id=pid))],
        [("⬅️ К товарам", A(act="prods"))],
    ]
    return f"{note}✏️ Так товар видят покупатели:\n\n{product_text(p)}{info}", kb_rows(*rows)


def pcats_screen(catalog: dict, pid: str):
    p = find_product(catalog, pid)
    if p is None:
        return prods_screen(catalog, "⚠️ Товар не найден.\n\n")
    mine = product_categories(p)
    rows = [
        [(("✅ " if c["id"] in mine else "▫️ ") + c["title"], A(act="pcat", id=pid, arg=c["id"]))]
        for c in catalog["categories"]
    ]
    rows.append([("⬅️ Готово", A(act="prod", id=pid))])
    text = f"🗂 В каких разделах показывать «{e(p['name'])}»?\n\nНажмите на раздел, чтобы включить или выключить его."
    return text, kb_rows(*rows)


def links_screen(catalog: dict, owner: str):
    links = links_of(catalog, owner)
    if links is None:
        return prods_screen(catalog, "⚠️ Товар не найден.\n\n")
    if owner == SHOP:
        where, back = "в разделе «О магазине»", A(act="home")
    else:
        where, back = f"у товара «{e(find_product(catalog, owner)['name'])}»", A(act="prod", id=owner)
    lines = [f"{i + 1}. {e(link['title'])}\n{e(link['url'])}" for i, link in enumerate(links)] or ["Кнопок пока нет."]
    rows = [[(f"🗑 Удалить: {link['title']}", A(act="ldel", id=owner, arg=str(i)))] for i, link in enumerate(links)]
    rows += [[("➕ Добавить кнопку", A(act="ladd", id=owner))], [("⬅️ Назад", back)]]
    return f"🔗 <b>Кнопки-ссылки</b> {where}\n\n" + "\n\n".join(lines), kb_rows(*rows)


def cats_screen(catalog: dict, note: str = ""):
    rows = [[(c["title"], A(act="cat", id=c["id"]))] for c in catalog["categories"]]
    rows += [[("➕ Добавить раздел", A(act="newcat"))], [("⬅️ Назад", A(act="home"))]]
    text = f"{note}🗂 <b>Разделы</b> — кнопки в главном меню бота.\n\nВыберите раздел, чтобы изменить его."
    return text, kb_rows(*rows)


def cat_screen(catalog: dict, cid: str):
    c = find_category(catalog, cid)
    if c is None:
        return cats_screen(catalog, "⚠️ Раздел не найден.\n\n")
    names = [p["name"] for p in catalog["products"] if cid in product_categories(p)]
    listing = "\n".join(f"• {e(n)}" for n in names) or "пока пусто"
    text = (
        f"🗂 Раздел <b>{e(c['title'])}</b>\n\nТовары в разделе:\n{listing}\n\n"
        "<i>Чтобы добавить товар в раздел, откройте товар → «🗂 Разделы».</i>"
    )
    return text, kb_rows(
        [("✏️ Переименовать", A(act="catname", id=cid))],
        [("⬆️ Выше", A(act="cmove", id=cid, arg="up")), ("⬇️ Ниже", A(act="cmove", id=cid, arg="down"))],
        [("🗑 Удалить раздел", A(act="cdel", id=cid))],
        [("⬅️ К разделам", A(act="cats"))],
    )


def confirm_screen(question: str, yes: A, no: A):
    return question, kb_rows([("🗑 Да, удалить", yes)], [("✖️ Нет, оставить", no)])


# ---------- вход ----------

@panel.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show(message, *home_screen())


@panel.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show(message, *home_screen("Отменено.\n\n"))


# ---------- кнопки админки ----------

@panel.callback_query(A.filter())
async def on_action(call: CallbackQuery, callback_data: A, state: FSMContext) -> None:
    await state.clear()
    act, oid, arg = callback_data.act, callback_data.id, callback_data.arg
    catalog = load_catalog()

    # навигация
    if act == "home":
        return await show(call, *home_screen())
    if act == "close":
        await call.message.delete()
        return await call.answer("Админ-панель закрыта. Открыть снова: /admin")
    if act == "prods":
        return await show(call, *prods_screen(catalog))
    if act == "stats":
        kb = kb_rows([("🔄 Обновить", A(act="stats"))], [("⬅️ Назад", A(act="home"))])
        return await show(call, stats.report(catalog), kb)
    if act == "prod":
        return await show(call, *prod_screen(catalog, oid))
    if act == "pcats":
        return await show(call, *pcats_screen(catalog, oid))
    if act == "links":
        return await show(call, *links_screen(catalog, oid))
    if act == "cats":
        return await show(call, *cats_screen(catalog))
    if act == "cat":
        return await show(call, *cat_screen(catalog, oid))

    # товары
    if act == "newprod":
        return await ask(call, state, "➕ <b>Новый товар</b>\n\nНапишите название товара:", A(act="prods"), kind="newprod")
    if act == "pfield":
        p = find_product(catalog, oid)
        if p is None or arg not in FIELDS:
            return await show(call, *prods_screen(catalog, "⚠️ Товар не найден.\n\n"))
        current = e(str(p.get(arg) or "—"))
        clear_hint = "" if arg in REQUIRED else "\n\nЧтобы очистить поле, отправьте <code>-</code>"
        if arg == "price" and p.get("sku") and ozon._credentials():
            clear_hint += ("\n\n⚠️ Цена этого товара обновляется автоматически с Ozon по артикулу — "
                           "ручная правка будет заменена ценой с Ozon. Чтобы отключить, очистите артикул.")
        prompt = f"✏️ <b>{FIELDS[arg]}</b> — «{e(p['name'])}»\n\nСейчас:\n{current}\n\nНапишите новое значение:{clear_hint}"
        return await ask(call, state, prompt, A(act="prod", id=oid), kind="pfield", id=oid, field=arg)
    if act == "pphoto":
        prompt = (
            "🖼 Пришлите новое фото товара — как обычную картинку (не файлом).\n\n"
            "Чтобы убрать фото, отправьте <code>-</code>"
        )
        return await ask(call, state, prompt, A(act="prod", id=oid), kind="pphoto", id=oid)
    if act == "pcat":
        p = find_product(catalog, oid)
        if p is not None:
            cats = product_categories(p)
            if arg in cats:
                cats.remove(arg)
            else:
                cats.append(arg)
            p["category"] = cats
            await save(call, catalog)
        return await show(call, *pcats_screen(catalog, oid))
    if act == "pmove":
        ids = [p["id"] for p in catalog["products"]]
        if oid in ids:
            move(catalog["products"], ids.index(oid), arg)
            await save(call, catalog)
        return await show(call, *prod_screen(catalog, oid))
    if act == "pdel":
        p = find_product(catalog, oid)
        if p is None:
            return await show(call, *prods_screen(catalog))
        return await show(call, *confirm_screen(
            f"Удалить товар «{e(p['name'])}»? Это нельзя отменить.", A(act="pdel2", id=oid), A(act="prod", id=oid)))
    if act == "pdel2":
        catalog["products"] = [p for p in catalog["products"] if p["id"] != oid]
        await save(call, catalog)
        return await show(call, *prods_screen(catalog, "🗑 Товар удалён.\n\n"))

    # кнопки-ссылки
    if act == "ladd":
        return await ask(
            call, state, "🔗 Напишите текст кнопки, например: <code>📸 Заказать в Instagram</code>",
            A(act="links", id=oid), kind="link_title", id=oid)
    if act == "ldel":
        links = links_of(catalog, oid)
        if links is not None and arg.isdigit() and int(arg) < len(links):
            links.pop(int(arg))
            await save(call, catalog)
        return await show(call, *links_screen(catalog, oid))

    # разделы
    if act == "newcat":
        return await ask(
            call, state, "➕ Напишите название нового раздела, например: <code>💄 Макияж</code>",
            A(act="cats"), kind="newcat")
    if act == "catname":
        c = find_category(catalog, oid)
        if c is None:
            return await show(call, *cats_screen(catalog))
        return await ask(
            call, state, f"✏️ Сейчас раздел называется «{e(c['title'])}».\n\nНапишите новое название:",
            A(act="cat", id=oid), kind="catname", id=oid)
    if act == "cmove":
        ids = [c["id"] for c in catalog["categories"]]
        if oid in ids:
            move(catalog["categories"], ids.index(oid), arg)
            await save(call, catalog)
        return await show(call, *cat_screen(catalog, oid))
    if act == "cdel":
        c = find_category(catalog, oid)
        if c is None:
            return await show(call, *cats_screen(catalog))
        return await show(call, *confirm_screen(
            f"Удалить раздел «{e(c['title'])}»?\n\nТовары не удалятся, но пропадут из этого раздела. "
            "Товары, у которых не останется разделов, покупатели не увидят.",
            A(act="cdel2", id=oid), A(act="cat", id=oid)))
    if act == "cdel2":
        catalog["categories"] = [c for c in catalog["categories"] if c["id"] != oid]
        for p in catalog["products"]:
            if oid in product_categories(p):
                p["category"] = [c for c in product_categories(p) if c != oid]
        await save(call, catalog)
        return await show(call, *cats_screen(catalog, "🗑 Раздел удалён.\n\n"))

    # тексты
    if act == "text" and oid in TEXTS:
        current = catalog["shop"].get(oid, "")
        prompt = (
            f"💬 <b>{TEXTS[oid]}</b> — сейчас текст такой:\n\n{current}\n\n———\n"
            "Пришлите новый текст. Жирный и курсив из Telegram сохранятся."
        )
        return await ask(call, state, prompt, A(act="home"), kind="text", id=oid)

    await call.answer()


# ---------- ввод значений ----------

@panel.message(Edit.value)
async def on_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind, oid = data.get("kind"), data.get("id", "")
    catalog = load_catalog()
    text = (message.text or "").strip()

    if kind == "pphoto":
        p = find_product(catalog, oid)
        if p is None:
            await state.clear()
            return await show(message, *prods_screen(catalog, "⚠️ Товар не найден.\n\n"))
        if message.photo:
            p["photo"] = message.photo[-1].file_id
        elif text == "-":
            p.pop("photo", None)
        else:
            return await message.answer("Пришлите фото картинкой (не файлом) или <code>-</code>, чтобы убрать фото.")
        await save(message, catalog)
        await state.clear()
        return await show(message, *prod_screen(catalog, oid, "✅ Фото обновлено.\n\n"))

    if not text:
        return await message.answer("Пришлите, пожалуйста, текст. Отмена: /cancel")

    if kind == "pfield":
        p = find_product(catalog, oid)
        field = data.get("field")
        if p is None or field not in FIELDS:
            await state.clear()
            return await show(message, *prods_screen(catalog, "⚠️ Товар не найден.\n\n"))
        if text == "-" and field not in REQUIRED:
            p.pop(field, None)
        elif text == "-":
            return await message.answer(f"Поле «{FIELDS[field]}» обязательное — напишите значение.")
        else:
            p[field] = text
        await save(message, catalog)
        await state.clear()
        return await show(message, *prod_screen(catalog, oid, f"✅ «{FIELDS[field]}» сохранено.\n\n"))

    if kind == "newprod":
        await state.update_data(kind="newprod_price", name=text)
        return await message.answer(
            f"Название: <b>{e(text)}</b>\n\nТеперь напишите цену, например: <code>890 ₽</code>",
            reply_markup=kb_rows([("✖️ Отмена", A(act="prods"))]))

    if kind == "newprod_price":
        first_cat = catalog["categories"][0]["id"] if catalog["categories"] else None
        product = {
            "id": new_id(catalog["products"], "p"),
            "category": [first_cat] if first_cat else [],
            "name": data["name"],
            "brand": catalog["shop"].get("name", ""),
            "price": text,
            "buy_links": copy.deepcopy(catalog["shop"].get("links", [])),
        }
        catalog["products"].append(product)
        await save(message, catalog)
        await state.clear()
        note = "✅ Товар добавлен! Теперь добавьте описание, фото и остальное — кнопками ниже.\n\n"
        return await show(message, *prod_screen(catalog, product["id"], note))

    if kind == "link_title":
        await state.update_data(kind="link_url", title=text)
        return await message.answer(
            f"Кнопка: <b>{e(text)}</b>\n\nТеперь пришлите ссылку (начинается с https://):",
            reply_markup=kb_rows([("✖️ Отмена", A(act="links", id=oid))]))

    if kind == "link_url":
        if not re.fullmatch(r"https?://\S+", text):
            return await message.answer("Это не похоже на ссылку. Пришлите ссылку, которая начинается с https://")
        links = links_of(catalog, oid)
        if links is None:
            await state.clear()
            return await show(message, *prods_screen(catalog, "⚠️ Товар не найден.\n\n"))
        links.append({"title": data["title"], "url": text})
        await save(message, catalog)
        await state.clear()
        return await show(message, *links_screen(catalog, oid))

    if kind == "newcat":
        catalog["categories"].append({"id": new_id(catalog["categories"], "c"), "title": text})
        await save(message, catalog)
        await state.clear()
        note = "✅ Раздел добавлен. Чтобы положить в него товары, откройте товар → «🗂 Разделы».\n\n"
        return await show(message, *cats_screen(catalog, note))

    if kind == "catname":
        c = find_category(catalog, oid)
        if c is not None:
            c["title"] = text
            await save(message, catalog)
        await state.clear()
        return await show(message, *cat_screen(catalog, oid))

    if kind == "text" and oid in TEXTS:
        catalog["shop"][oid] = message.html_text
        await save(message, catalog)
        await state.clear()
        return await show(message, *home_screen(f"✅ Текст «{TEXTS[oid]}» обновлён.\n\n"))

    await state.clear()
    await show(message, *home_screen())
