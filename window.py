"""Единое окно интерфейса.

В чате всегда отображается ровно одно сообщение бота: все экраны
рендерятся редактированием текущего окна; пуш-уведомления заменяют окно
новым сообщением; ввод пользователя удаляется хендлерами.
Идентификатор окна хранится в БД и переживает перезапуск.

Табличные экраны отправляются нативными таблицами (Bot API Rich Messages):
новые сообщения — через sendRichMessage (см. send_table), обновление
существующего окна — через параметр rich_message метода editMessageText.
Дерево RichBlock* вручную не собирается — Telegram строит таблицу
из классической HTML-разметки <table>.
"""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.methods import SendRichMessage
from aiogram.types import CallbackQuery, InputRichMessage, Message

from db import Database
from utils import table_html

log = logging.getLogger(__name__)


async def send_table(bot: Bot, chat_id: int, headers=None, rows=None,
                     *, html: str | None = None, reply_markup=None) -> Message:
    """Отправляет нативную таблицу через sendRichMessage.

    Собирает классический HTML-тег <table> из headers/rows; готовые
    rich-экраны (заголовок + таблица + итоги) передаются через html.
    """
    if html is None:
        html = table_html(headers, rows)
    return await bot(SendRichMessage(
        chat_id=chat_id,
        rich_message=InputRichMessage(html=html),
        reply_markup=reply_markup,
    ))


async def _edit_window(bot: Bot, chat_id: int, message_id: int,
                       text: str, reply_markup, rich: bool) -> None:
    if rich:
        # editRichMessage не существует: rich-контент передаётся
        # параметром rich_message существующего editMessageText
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            rich_message=InputRichMessage(html=text),
            reply_markup=reply_markup,
        )
    else:
        await bot.edit_message_text(
            text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup
        )


async def _send_window(bot: Bot, chat_id: int, text: str,
                       reply_markup, rich: bool) -> Message:
    if rich:
        return await send_table(bot, chat_id, html=text, reply_markup=reply_markup)
    return await bot.send_message(chat_id, text, reply_markup=reply_markup)


async def render(
    bot: Bot,
    db: Database,
    chat_id: int,
    text: str,
    reply_markup=None,
    *,
    new_message: bool = False,
    rich: bool = False,
) -> None:
    """Отрисовывает окно чата.

    По умолчанию редактирует текущее окно. new_message=True отправляет
    новое сообщение (нужно для пушей — редактирование не даёт уведомления),
    удаляя прежнее окно. rich=True трактует text как HTML rich-сообщения.
    """
    window_id = await db.get_window(chat_id)
    if window_id and not new_message:
        try:
            await _edit_window(bot, chat_id, window_id, text, reply_markup, rich)
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
            # окно потеряно или нередактируемо — заменяем новым сообщением
    if window_id:
        try:
            await bot.delete_message(chat_id, window_id)
        except TelegramAPIError:
            pass
    message = await _send_window(bot, chat_id, text, reply_markup, rich)
    await db.set_window(chat_id, message.message_id)


async def render_cb(bot: Bot, db: Database, cb: CallbackQuery, text: str,
                    reply_markup=None, *, rich: bool = False) -> None:
    """Отрисовывает окно в ответ на нажатие кнопки.

    Нажатое сообщение становится окном; прежнее окно, если это другое
    сообщение, удаляется — инвариант «одного окна» восстанавливается,
    даже если пользователь нажал кнопку на устаревшем сообщении.
    """
    chat_id = cb.message.chat.id
    message_id = cb.message.message_id
    window_id = await db.get_window(chat_id)
    if window_id and window_id != message_id:
        try:
            await bot.delete_message(chat_id, window_id)
        except TelegramAPIError:
            pass
    try:
        await _edit_window(bot, chat_id, message_id, text, reply_markup, rich)
        await db.set_window(chat_id, message_id)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await db.set_window(chat_id, message_id)
            return
        # сообщение нельзя отредактировать (например, документ) — заменяем
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramAPIError:
            pass
        message = await _send_window(bot, chat_id, text, reply_markup, rich)
        await db.set_window(chat_id, message.message_id)


async def delete_user_message(message: Message) -> None:
    """Удаляет сообщение пользователя, сохраняя в чате только окно."""
    try:
        await message.delete()
    except TelegramAPIError:
        pass
