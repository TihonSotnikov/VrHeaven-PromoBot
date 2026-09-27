"""Вспомогательные функции: MarkdownV2, деньги, даты, пароли."""

import hashlib
import hmac
import html
import re
import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- MarkdownV2

_MD_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def esc(text) -> str:
    """Экранирует спецсимволы MarkdownV2 в обычном тексте."""
    return _MD_RE.sub(r"\\\1", str(text))


def esc_code(text) -> str:
    """Экранирование внутри `inline code` и ```pre``` блоков."""
    return str(text).replace("\\", "\\\\").replace("`", "\\`")


def inline_code(text) -> str:
    return "`" + esc_code(text) + "`"


def esc_html(text) -> str:
    """Экранирует HTML-контент rich-сообщений (Bot API Rich Messages)."""
    return html.escape(str(text), quote=False)


def table_html(headers: list[str], rows: list[list]) -> str:
    """Классический HTML-тег <table> для нативных таблиц (Rich Messages)."""
    head = "".join(f"<th>{esc_html(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{esc_html(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


# ------------------------------------------------------------------- Деньги

def fmt_num(amount: float) -> str:
    """1234567.5 -> '1 234 567,50'. Без спецсимволов MarkdownV2."""
    amount = round(float(amount), 2)
    if amount == int(amount):
        s = f"{int(amount):,}"
    else:
        s = f"{amount:,.2f}"
    return s.replace(",", " ").replace(".", ",")


def fmt_money(amount: float) -> str:
    return f"{fmt_num(amount)} ₽"


def fmt_percent(percent: float) -> str:
    percent = round(float(percent), 2)
    if percent == int(percent):
        return f"{int(percent)}%"
    return f"{percent:.2f}".rstrip("0").replace(".", ",") + "%"


def parse_amount(text: str) -> float | None:
    """Разбор суммы из ввода: '1 500', '1500.50', '1500,50'. None — если некорректно."""
    t = text.strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        value = round(float(t), 2)
    except ValueError:
        return None
    if not (0 < value < 100_000_000):
        return None
    return value


# --------------------------------------------------------------------- Даты

def utcnow_iso() -> str:
    """Момент «сейчас» в UTC для хранения в БД (ISO 8601, сортируется строкой)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fmt_dt(iso: str, tz: ZoneInfo, fmt: str = "%d.%m.%Y %H:%M") -> str:
    return datetime.fromisoformat(iso).astimezone(tz).strftime(fmt)


# ------------------------------------------------------------------- Пароли

_PWD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"


def gen_password(length: int = 10) -> str:
    return "".join(secrets.choice(_PWD_ALPHABET) for _ in range(length))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return hmac.compare_digest(digest, expected)
