"""Копия каталога в Telegram.

На бесплатном хостинге диск стирается при каждом перезапуске, и правки из
админки пропали бы. Поэтому каталог хранится ещё и в чате первого админа:
закреплённое сообщение с файлом products.json. После каждой правки бот
обновляет этот файл (редактирует то же сообщение), а при запуске — забирает
его оттуда.
"""

import asyncio
import json
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InputMediaDocument, Message

from catalog import CATALOG_FILE, admin_ids

FILE_NAME = "products.json"
_lock = asyncio.Lock()


def _owner() -> int | None:
    ids = sorted(admin_ids())
    return ids[0] if ids else None


async def _pinned_backup(bot: Bot, chat_id: int) -> Message | None:
    chat = await bot.get_chat(chat_id)
    msg = chat.pinned_message
    if msg and msg.document and msg.document.file_name == FILE_NAME:
        return msg
    return None


async def pull(bot: Bot) -> None:
    """При запуске: если в Telegram есть копия каталога — берём её вместо файла на диске."""
    owner = _owner()
    if owner is None:
        return
    try:
        msg = await _pinned_backup(bot, owner)
        if msg is None:
            logging.info("Копии каталога в Telegram ещё нет — создаю из файла на диске")
            await push(bot)
            return
        data = (await bot.download(msg.document.file_id)).read()
        json.loads(data)  # убеждаемся, что файл целый
        CATALOG_FILE.write_bytes(data)
        logging.info("Каталог загружен из копии в Telegram")
    except (TelegramAPIError, ValueError) as err:
        logging.warning("Не удалось загрузить копию каталога из Telegram, использую файл на диске: %s", err)


async def push(bot: Bot) -> None:
    """После правки: обновляем закреплённый файл каталога в чате админа."""
    owner = _owner()
    if owner is None:
        return
    async with _lock:
        stamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        caption = f"🗂 Копия каталога от {stamp}\nНе удаляйте и не открепляйте это сообщение — бот хранит здесь каталог."
        doc = BufferedInputFile(CATALOG_FILE.read_bytes(), filename=FILE_NAME)
        try:
            msg = await _pinned_backup(bot, owner)
            if msg is not None:
                await bot.edit_message_media(
                    InputMediaDocument(media=doc, caption=caption), chat_id=owner, message_id=msg.message_id
                )
                return
            sent = await bot.send_document(owner, doc, caption=caption, disable_notification=True)
            await bot.pin_chat_message(owner, sent.message_id, disable_notification=True)
        except TelegramAPIError as err:
            logging.warning("Не удалось сохранить копию каталога в Telegram: %s", err)
