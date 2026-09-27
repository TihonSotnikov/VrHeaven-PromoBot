"""Загрузка конфигурации из .env."""

import os
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    tz: ZoneInfo
    db_path: str


def load_config() -> Config:
    load_dotenv()

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN не задан — заполните .env (см. .env.example)")

    raw_ids = os.getenv("ADMIN_IDS", "").strip()
    try:
        admin_ids = frozenset(int(x) for x in re.split(r"[,\s]+", raw_ids) if x)
    except ValueError:
        raise RuntimeError("ADMIN_IDS должен содержать числовые Telegram ID через запятую")
    if not admin_ids:
        raise RuntimeError("ADMIN_IDS не задан — заполните .env (см. .env.example)")

    tz_name = os.getenv("TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        raise RuntimeError(f"Неизвестный часовой пояс: {tz_name}")

    db_path = os.getenv("DB_PATH", "partnerbot.db").strip() or "partnerbot.db"

    return Config(bot_token=token, admin_ids=admin_ids, tz=tz, db_path=db_path)
