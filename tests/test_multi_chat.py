"""Несколько Telegram-аккаунтов у одного партнёра (partner_chats)."""

import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from helpers import FakeBot, fake_cb, fake_msg, make_state

from db import Database
from handlers.admin import order_confirm, partner_pwd_regen
from handlers.admin import AddOrderSG
from handlers.partner import LoginSG, login_password
from utils import hash_password

CONFIG = SimpleNamespace(tz=ZoneInfo("Europe/Moscow"))
ADMIN_CHAT = 1
CHAT_A = 777
CHAT_B = 888


async def test_second_login_does_not_kick_first_device(db, password_hash):
    """Главный сценарий: два совладельца, оба остаются в кабинете."""
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    await db.bind_chat(pid, CHAT_A)
    await db.bind_chat(pid, CHAT_B)

    assert (await db.get_partner_by_chat(CHAT_A))["id"] == pid
    assert (await db.get_partner_by_chat(CHAT_B))["id"] == pid
    assert await db.chats_for_partner(pid) == [CHAT_A, CHAT_B]


async def test_rebinding_chat_moves_only_that_chat(db, password_hash):
    """Чат уходит к другому партнёру; остальные чаты первого не трогаются."""
    pid1 = await db.create_partner("vr101", "Club X", "", password_hash)
    pid2 = await db.create_partner("vr202", "Club Y", "", password_hash)
    await db.bind_chat(pid1, CHAT_A)
    await db.bind_chat(pid1, CHAT_B)

    await db.bind_chat(pid2, CHAT_B)
    assert (await db.get_partner_by_chat(CHAT_B))["id"] == pid2
    assert await db.chats_for_partner(pid1) == [CHAT_A]


async def test_delete_partner_unbinds_all_chats(db, password_hash):
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    await db.bind_chat(pid, CHAT_A)
    await db.bind_chat(pid, CHAT_B)

    await db.delete_partner(pid)
    assert await db.get_partner_by_chat(CHAT_A) is None
    assert await db.get_partner_by_chat(CHAT_B) is None
    assert await db.chats_for_partner(pid) == []


async def test_order_push_reaches_all_devices(db, password_hash):
    """Пуш о новом заказе приходит на оба устройства партнёра."""
    bot = FakeBot()
    state = make_state(ADMIN_CHAT)
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    await db.bind_chat(pid, CHAT_A)
    await db.bind_chat(pid, CHAT_B)
    await db.set_window(ADMIN_CHAT, 50)
    await state.set_state(AddOrderSG.confirm)
    await state.update_data(partner_id=pid, promo_code="vr101",
                            amount=1000.0, commission=100.0)

    await order_confirm(fake_cb("ao:ok", message_id=50), state, db, bot)

    push_chats = {m[0] for m in bot.sent}
    assert {CHAT_A, CHAT_B} <= push_chats
    assert "доставлено: 2 из 2" in bot.edited[-1][2]


async def test_password_regen_revokes_all_devices(db, password_hash):
    """Смена пароля отзывает доступ: все устройства требуют повторного входа."""
    bot = FakeBot()
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    await db.bind_chat(pid, CHAT_A)
    await db.bind_chat(pid, CHAT_B)
    await db.set_window(ADMIN_CHAT, 50)

    await partner_pwd_regen(fake_cb(f"ap:pwdok:{pid}", message_id=50), db, bot)
    assert await db.chats_for_partner(pid) == []
    assert await db.get_partner_by_chat(CHAT_A) is None


async def test_login_from_new_device_notifies_existing(db):
    """Вход с нового устройства: прежние чаты получают предупреждение."""
    bot = FakeBot()
    pid = await db.create_partner("vr101", "Club X", "", hash_password("secret"))
    await db.bind_chat(pid, CHAT_A)

    state = make_state(CHAT_B)
    await state.set_state(LoginSG.password)
    await state.update_data(partner_id=pid, attempts=0)
    await login_password(fake_msg("secret", chat_id=CHAT_B), state, db, bot)

    # оба устройства в кабинете, старое предупреждено
    assert await db.chats_for_partner(pid) == [CHAT_A, CHAT_B]
    warnings = [m for m in bot.sent if m[0] == CHAT_A]
    assert len(warnings) == 1 and "нового устройства" in warnings[0][2]


async def test_relogin_same_device_does_not_notify(db):
    """Повторный вход с уже привязанного чата не рассылает предупреждений."""
    bot = FakeBot()
    pid = await db.create_partner("vr101", "Club X", "", hash_password("secret"))
    await db.bind_chat(pid, CHAT_A)
    await db.bind_chat(pid, CHAT_B)

    state = make_state(CHAT_B)
    await state.set_state(LoginSG.password)
    await state.update_data(partner_id=pid, attempts=0)
    await login_password(fake_msg("secret", chat_id=CHAT_B), state, db, bot)

    assert [m for m in bot.sent if m[0] == CHAT_A] == []


async def test_migration_v3_to_v4_moves_bindings(tmp_path, password_hash):
    """База со старой колонкой partners.chat_id: привязки переезжают в partner_chats."""
    path = str(tmp_path / "v3.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE partners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_code TEXT NOT NULL,
            venue_name TEXT NOT NULL,
            contact TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            commission_percent REAL NOT NULL DEFAULT 10,
            is_active INTEGER NOT NULL DEFAULT 1,
            deleted_at TEXT,
            chat_id INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE payouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_id INTEGER NOT NULL REFERENCES partners(id),
            amount REAL NOT NULL,
            orders_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_id INTEGER NOT NULL REFERENCES partners(id),
            amount REAL NOT NULL,
            commission REAL NOT NULL,
            payout_id INTEGER REFERENCES payouts(id),
            cancelled_at TEXT,
            created_at TEXT NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO partners (promo_code, venue_name, password_hash, chat_id, created_at)"
        " VALUES ('vr101', 'Club X', 'hash', 777, '2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO partners (promo_code, venue_name, password_hash, chat_id,"
        " deleted_at, created_at)"
        " VALUES ('vr202', 'Club Y', 'hash', 888,"
        " '2026-01-05T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    db = Database(path)
    await db.init()
    try:
        partner = await db.get_partner_by_code("vr101")
        assert (await db.get_partner_by_chat(777))["id"] == partner["id"]
        # чат удалённого партнёра не переносится — доступ не воскресает
        assert await db.get_partner_by_chat(888) is None
        # колонка chat_id из partners удалена
        cur = await db.conn.execute("PRAGMA table_info(partners)")
        columns = [row[1] for row in await cur.fetchall()]
        assert "chat_id" not in columns

        # новые привязки работают поверх мигрированной базы
        await db.bind_chat(partner["id"], 999)
        assert await db.chats_for_partner(partner["id"]) == [777, 999]
    finally:
        await db.close()
