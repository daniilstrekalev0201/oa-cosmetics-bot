"""Автообновление цен с Ozon через официальный Seller API.

Работает, только если в переменных окружения заданы OZON_CLIENT_ID и OZON_API_KEY
(ключ создаётся в кабинете Ozon Seller → Настройки → Seller API). Без них — молчит.

Раз в OZON_SYNC_HOURS часов (по умолчанию 3) бот берёт цены товаров по их артикулам
(поле sku в каталоге), и если цена изменилась — обновляет каталог (а значит, бот и сайт)
и пишет владельцу, что поменялось.
"""

import asyncio
import html
import logging
import os

import aiohttp
from aiogram import Bot

import cloud
from catalog import admin_list, load_catalog, save_catalog

API_URL = "https://api-seller.ozon.ru/v3/product/info/list"
_last_error = ""


def _credentials() -> tuple[str, str] | None:
    client_id, api_key = os.getenv("OZON_CLIENT_ID", "").strip(), os.getenv("OZON_API_KEY", "").strip()
    return (client_id, api_key) if client_id and api_key else None


def format_price(value) -> str | None:
    """'720.0000' → '720 ₽', 1490 → '1 490 ₽'."""
    try:
        rub = round(float(value))
    except (TypeError, ValueError):
        return None
    if rub <= 0:
        return None
    return f"{rub:,}".replace(",", " ") + " ₽"


def _item_skus(item: dict) -> set[str]:
    skus = {str(item["sku"])} if item.get("sku") else set()
    for source in item.get("sources") or []:
        if source.get("sku"):
            skus.add(str(source["sku"]))
    return skus


async def fetch_prices(skus: list[str]) -> dict[str, str]:
    """Артикул Ozon → цена для покупателя в виде «720 ₽»."""
    creds = _credentials()
    if not creds or not skus:
        return {}
    headers = {"Client-Id": creds[0], "Api-Key": creds[1], "Content-Type": "application/json"}
    body = {"sku": [int(s) for s in skus if s.isdigit()]}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.post(API_URL, json=body, headers=headers) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise RuntimeError(f"Ozon ответил {resp.status}: {str(data)[:300]}")
    prices = {}
    for item in data.get("items") or data.get("result", {}).get("items") or []:
        price = format_price(item.get("price"))
        if price:
            for sku in _item_skus(item):
                prices[sku] = price
    return prices


async def sync(bot: Bot) -> list[str]:
    """Сверяет цены с Ozon. Возвращает список изменений вида «Скраб: 705 ₽ → 720 ₽»."""
    catalog = load_catalog()
    by_sku = {str(p["sku"]): p for p in catalog["products"] if p.get("sku")}
    prices = await fetch_prices(list(by_sku))
    changes = []
    for sku, price in prices.items():
        product = by_sku.get(sku)
        if product and product.get("price") != price:
            changes.append(f"{product['name']}: {product.get('price', '—')} → {price}")
            product["price"] = price
    missing = [p["name"] for s, p in by_sku.items() if s not in prices]
    if missing:
        logging.info("Ozon не вернул цену для: %s (нет в продаже или чужой артикул)", ", ".join(missing))
    if changes:
        save_catalog(catalog)
        await cloud.push(bot)
        logging.info("Цены обновлены с Ozon: %s", "; ".join(changes))
        await _notify(bot, "💰 <b>Цены обновлены с Ozon</b>\n\n" + "\n".join(f"• {html.escape(c)}" for c in changes))
    return changes


async def _notify(bot: Bot, text: str) -> None:
    owner = admin_list()[0] if admin_list() else None
    if owner:
        try:
            await bot.send_message(owner, text)
        except Exception as err:  # noqa: BLE001 — уведомление не должно ронять синхронизацию
            logging.warning("Не удалось отправить уведомление о ценах: %s", err)


async def run_forever(bot: Bot) -> None:
    """Фоновая задача: синхронизация при запуске и затем каждые OZON_SYNC_HOURS часов."""
    global _last_error
    if not _credentials():
        logging.info("Автообновление цен с Ozon выключено: не заданы OZON_CLIENT_ID и OZON_API_KEY")
        return
    hours = float(os.getenv("OZON_SYNC_HOURS", "3"))
    logging.info("Автообновление цен с Ozon включено, каждые %s ч.", hours)
    while True:
        try:
            changes = await sync(bot)
            if not changes:
                logging.info("Цены на Ozon не изменились")
            _last_error = ""
        except Exception as err:  # noqa: BLE001
            logging.warning("Не удалось получить цены с Ozon: %s", err)
            if str(err) != _last_error:  # не спамим одной и той же ошибкой
                _last_error = str(err)
                await _notify(bot, f"⚠️ Не удалось получить цены с Ozon:\n<code>{html.escape(str(err)[:300])}</code>")
        await asyncio.sleep(hours * 3600)
