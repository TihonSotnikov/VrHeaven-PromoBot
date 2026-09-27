"""Точка входа: запуск бота в режиме long polling."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from config import load_config
from db import Database
from handlers import admin, partner
from scheduler import setup_scheduler


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()

    db = Database(config.db_path)
    await db.init()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2),
    )
    dp = Dispatcher()
    dp["config"] = config
    dp["db"] = db
    # Порядок важен: сначала админский роутер (фильтр по ID), затем партнёрский
    dp.include_router(admin.router)
    dp.include_router(partner.router)

    scheduler = setup_scheduler(bot, db, config)
    scheduler.start()

    await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
