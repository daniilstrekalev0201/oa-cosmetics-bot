"""Работа с каталогом products.json: чтение, сохранение и общие помощники."""

import html
import json
import os
import shutil
from pathlib import Path

from aiogram.types import FSInputFile

CATALOG_FILE = Path(__file__).with_name("products.json")
BACKUP_FILE = CATALOG_FILE.with_name("products.backup.json")


# Админ по умолчанию — на хостинге без переменных окружения бот всё равно знает владельца.
DEFAULT_ADMIN_IDS = "974443828"


def admin_ids() -> set[int]:
    """Telegram ID админов из ADMIN_IDS в .env (через запятую), иначе — DEFAULT_ADMIN_IDS."""
    raw = os.getenv("ADMIN_IDS") or DEFAULT_ADMIN_IDS
    return {int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()}


def load_catalog() -> dict:
    with CATALOG_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def save_catalog(catalog: dict) -> None:
    """Сохраняет каталог, предварительно копируя прошлую версию в products.backup.json."""
    if CATALOG_FILE.exists():
        shutil.copyfile(CATALOG_FILE, BACKUP_FILE)
    tmp = CATALOG_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, CATALOG_FILE)


def find_product(catalog: dict, product_id: str) -> dict | None:
    return next((p for p in catalog["products"] if p["id"] == product_id), None)


def find_category(catalog: dict, cat_id: str) -> dict | None:
    return next((c for c in catalog["categories"] if c["id"] == cat_id), None)


def product_categories(product: dict) -> list[str]:
    """Поле category может быть строкой или списком — товар бывает в нескольких категориях."""
    cat = product.get("category", [])
    return list(cat) if isinstance(cat, list) else [cat]


def photo_input(photo: str | None) -> str | FSInputFile | None:
    """Фото товара: ссылка, файл рядом с ботом или file_id фото, загруженного через админку."""
    if not photo:
        return None
    if photo.startswith(("http://", "https://")):
        return photo
    path = CATALOG_FILE.parent / photo
    if path.is_file():
        return FSInputFile(path)
    return photo  # file_id Telegram


def product_text(product: dict) -> str:
    e = html.escape
    lines = [f"<b>{e(product['name'])}</b>"]
    if product.get("brand"):
        lines.append(f"<i>{e(product['brand'])}</i>")
    lines.append("")
    lines.append(f"💰 <b>Цена:</b> {e(product['price'])}")
    if product.get("volume"):
        lines.append(f"📦 <b>Объём:</b> {e(product['volume'])}")
    if product.get("sku"):
        # <code> — в Telegram номер копируется одним нажатием
        lines.append(f"🔢 <b>Артикул Ozon:</b> <code>{e(product['sku'])}</code>")
    if product.get("description"):
        lines += ["", e(product["description"])]
    if product.get("composition"):
        lines += ["", f"🌿 <b>Состав:</b> {e(product['composition'])}"]
    if product.get("how_to_use"):
        lines += ["", f"🧴 <b>Применение:</b> {e(product['how_to_use'])}"]
    if product.get("where_to_buy"):
        lines += ["", f"🛍 <b>Где купить:</b> {e(product['where_to_buy'])}"]
    return "\n".join(lines)
