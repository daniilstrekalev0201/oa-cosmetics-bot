"""Напоминание об оплате сервера Bothost — только админам.

Дата окончания оплаты хранится в каталоге (catalog["billing"]["paid_until"]), поэтому
переживает перезапуски. Админы видят её в /admin и получают напоминания за 3 дня,
за 1 день, в день окончания и каждый день после, пока не нажмут «✅ Оплатил».
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import cloud
from catalog import LOCAL_TZ, admin_list, load_catalog, save_catalog

DEFAULT_PAID_UNTIL = "2026-11-07"  # тариф «Базовый» оплачен 08.10.2026 на 30 дней
PERIOD_DAYS = 30
PRICE = "99 ₽"
PAY_URL = "https://bothost.ru/pricing.php"
REMIND_DAYS_BEFORE = (3, 1, 0)
REMIND_FROM_HOUR = 10  # не будим ночью
CHECK_EVERY_SECONDS = 3600


def _today() -> date:
    return datetime.now(LOCAL_TZ).date()


def paid_until(catalog: dict) -> date:
    raw = catalog.get("billing", {}).get("paid_until", DEFAULT_PAID_UNTIL)
    return date.fromisoformat(raw)


def days_left(catalog: dict) -> int:
    return (paid_until(catalog) - _today()).days


def set_paid_until(catalog: dict, value: date) -> None:
    billing = catalog.setdefault("billing", {})
    billing["paid_until"] = value.isoformat()
    billing.pop("reminded", None)


def extend(catalog: dict) -> date:
    """«Оплатил»: +30 дней от даты окончания (или от сегодня, если уже просрочено)."""
    new = max(paid_until(catalog), _today()) + timedelta(days=PERIOD_DAYS)
    set_paid_until(catalog, new)
    return new


def _days(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} дня"
    return f"{n} дней"


def status_line(catalog: dict) -> str:
    left, until = days_left(catalog), paid_until(catalog)
    if left > 3:
        return f"💳 Сервер оплачен до {until:%d.%m.%Y} — осталось {_days(left)}"
    if left > 0:
        return f"⚠️ Сервер оплачен до {until:%d.%m.%Y} — осталось {_days(left)}, пора продлить"
    if left == 0:
        return f"⚠️ Оплата сервера заканчивается сегодня ({until:%d.%m.%Y})"
    return f"❗️ Оплата сервера закончилась {until:%d.%m.%Y}"


def reminder_text(catalog: dict) -> str:
    left, until = days_left(catalog), paid_until(catalog)
    if left > 0:
        head = f"💳 Через {_days(left)} ({until:%d.%m}) заканчивается оплата сервера Bothost."
    elif left == 0:
        head = f"💳 Сегодня ({until:%d.%m}) заканчивается оплата сервера Bothost."
    else:
        head = f"❗️ Оплата сервера Bothost закончилась {until:%d.%m}."
    return (
        f"{head}\n\nЕсли не продлить, бот и сайт перестанут работать.\n"
        f"Продлить: bothost.ru → «Тарифы» → «Базовый», {PRICE}.\n\n"
        "После оплаты нажмите «✅ Оплатил» — дата сдвинется на 30 дней.\n"
        "<i>Это сообщение видят только админы.</i>"
    )


def reminder_kb(paid_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Открыть Bothost", url=PAY_URL)],
        [InlineKeyboardButton(text="✅ Оплатил (+30 дней)", callback_data=paid_callback)],
    ])


def _due_stage(catalog: dict) -> str | None:
    """Какое напоминание пора отправить сегодня (или None)."""
    left = days_left(catalog)
    if left in REMIND_DAYS_BEFORE or left < 0:
        return f"{_today().isoformat()}:{left}"
    return None


async def check_and_remind(bot: Bot) -> bool:
    if datetime.now(LOCAL_TZ).hour < REMIND_FROM_HOUR:
        return False
    catalog = load_catalog()
    stage = _due_stage(catalog)
    if stage is None or catalog.get("billing", {}).get("reminded") == stage:
        return False
    from admin import A  # здесь, а не наверху: admin сам импортирует billing
    kb = reminder_kb(A(act="paid").pack())
    for uid in admin_list():
        try:
            await bot.send_message(uid, reminder_text(catalog), reply_markup=kb)
        except Exception as err:  # noqa: BLE001 — один недоступный админ не мешает остальным
            logging.warning("Не удалось отправить напоминание об оплате %s: %s", uid, err)
    catalog.setdefault("billing", {})["reminded"] = stage
    save_catalog(catalog)
    await cloud.push(bot)
    logging.info("Отправлено напоминание об оплате Bothost (%s)", stage)
    return True


async def run_forever(bot: Bot) -> None:
    logging.info("Напоминание об оплате: %s", status_line(load_catalog()))
    while True:
        try:
            await check_and_remind(bot)
        except Exception:  # noqa: BLE001
            logging.exception("Ошибка проверки оплаты")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
