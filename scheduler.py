"""Плановые задачи: отчёты в дни выплат и ежедневный бэкап базы."""

import logging
import os
import tempfile
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import keyboards as kb
from config import Config
from db import Database
from reports import admin_summary_text, partner_period_text
from utils import esc, esc_html
from window import render

log = logging.getLogger(__name__)


def setup_scheduler(bot: Bot, db: Database, config: Config) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=str(config.tz))
    scheduler.add_job(
        payout_day_job,
        CronTrigger(day="1,15", hour=10, minute=0, timezone=str(config.tz)),
        args=(bot, db, config),
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        backup_job,
        CronTrigger(hour=3, minute=0, timezone=str(config.tz)),
        args=(bot, db, config),
        misfire_grace_time=3600,
    )
    return scheduler


async def payout_day_job(bot: Bot, db: Database, config: Config) -> None:
    """1-го и 15-го в 10:00: сводка админам, отчёты партнёрам."""
    today = datetime.now(config.tz).strftime("%d.%m.%Y")
    summary = await admin_summary_text(db)
    text = f"<p><b>День выплат · {esc_html(today)}</b></p>" + summary
    for admin_id in config.admin_ids:
        try:
            await render(bot, db, admin_id, text, kb.to_admin_menu_kb(),
                         new_message=True, rich=True)
        except TelegramAPIError as e:
            log.warning("Не удалось отправить сводку админу %s: %s", admin_id, e)

    for row in await db.unpaid_summary():
        if row["orders_count"] == 0:
            continue
        chats = await db.chats_for_partner(row["id"])
        if not chats:
            continue
        partner = await db.get_partner(row["id"])
        report = await partner_period_text(db, partner, config.tz,
                                           title="Учётный период закрыт")
        report += "<p>Выплата будет проведена в ближайшее время</p>"
        for chat_id in chats:
            try:
                await render(bot, db, chat_id, report,
                             kb.to_partner_menu_kb(), new_message=True, rich=True)
            except TelegramAPIError as e:
                log.warning("Не удалось отправить отчёт партнёру %s в чат %s: %s",
                            row["promo_code"], chat_id, e)


async def backup_job(bot: Bot, db: Database, config: Config) -> None:
    """Ежедневно в 03:00: копия SQLite-файла админам в чат.

    Документ становится текущим окном чата: прежнее окно удаляется,
    а идентификатор окна переводится на сообщение с файлом.
    """
    stamp = datetime.now(config.tz).strftime("%Y-%m-%d")
    path = os.path.join(tempfile.gettempdir(), f"partnerbot_backup_{stamp}.db")
    if os.path.exists(path):
        os.remove(path)
    await db.conn.commit()
    await db.conn.execute("VACUUM INTO ?", (path,))
    try:
        document = FSInputFile(path, filename=f"backup_{stamp}.db")
        for admin_id in config.admin_ids:
            try:
                message = await bot.send_document(
                    admin_id, document,
                    caption=f"Резервная копия базы данных · {esc(stamp)}",
                    reply_markup=kb.to_admin_menu_kb(),
                )
                old_window = await db.get_window(admin_id)
                if old_window:
                    try:
                        await bot.delete_message(admin_id, old_window)
                    except TelegramAPIError:
                        pass
                await db.set_window(admin_id, message.message_id)
            except TelegramAPIError as e:
                log.warning("Не удалось отправить бэкап админу %s: %s", admin_id, e)
    finally:
        if os.path.exists(path):
            os.remove(path)
