"""Мягкая отмена заказа: исключение из расчётов при сохранении истории."""

import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from helpers import FakeBot, fake_cb

from db import Database
from handlers.admin import order_cancel_confirm, order_cancel_pick
from utils import hash_password

CONFIG = SimpleNamespace(tz=ZoneInfo("Europe/Moscow"))
ADMIN_CHAT = 1
PARTNER_CHAT = 777


async def test_cancel_keeps_row_and_excludes_from_calculations(db, password_hash):
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    kept = await db.add_order(pid, 1000, 100)
    cancelled = await db.add_order(pid, 2000, 200)

    assert await db.cancel_order(cancelled)

    total = await db.unpaid_total(pid)
    assert total["orders_count"] == 1
    assert total["turnover"] == 1000
    assert total["commission_sum"] == 100
    assert [o["id"] for o in await db.unpaid_orders(pid)] == [kept]
    assert [o["id"] for o in await db.last_orders()] == [kept]

    summary = await db.unpaid_summary()
    assert summary[0]["orders_count"] == 1

    # история сохранена: заказ остаётся в экспорте с датой отмены
    exported = {o["id"]: o for o in await db.export_orders()}
    assert len(exported) == 2
    assert exported[cancelled]["cancelled_at"] is not None
    assert exported[kept]["cancelled_at"] is None


async def test_double_cancel_returns_false(db, password_hash):
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    oid = await db.add_order(pid, 1000, 100)
    assert await db.cancel_order(oid)
    assert not await db.cancel_order(oid)
    assert not await db.cancel_order(999)  # несуществующий заказ


async def test_cancelled_order_not_swept_into_payout(db, password_hash):
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    kept = await db.add_order(pid, 1000, 100)
    cancelled = await db.add_order(pid, 2000, 200)
    await db.cancel_order(cancelled)

    payout_id, amount, count = await db.create_payout(pid)
    assert amount == 100
    assert count == 1
    # отменённый заказ не привязан к выплате — данные не расходятся
    assert (await db.get_order(cancelled))["payout_id"] is None
    assert (await db.get_order(kept))["payout_id"] == payout_id


async def test_payout_impossible_when_all_orders_cancelled(db, password_hash):
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    oid = await db.add_order(pid, 1000, 100)
    await db.cancel_order(oid)
    assert await db.create_payout(pid) is None


async def test_cancel_paid_order_keeps_payout_untouched(db, password_hash):
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    oid = await db.add_order(pid, 1000, 100)
    payout_id, amount, _ = await db.create_payout(pid)

    assert await db.cancel_order(oid)
    order = await db.get_order(oid)
    assert order["payout_id"] == payout_id  # заказ остаётся в своей выплате
    payout = (await db.payouts_for_partner(pid))[0]
    assert payout["amount"] == amount  # запись выплаты не корректируется
    assert (await db.unpaid_total(pid))["orders_count"] == 0


async def test_cancel_flow_pushes_once_and_blocks_double_press(db, password_hash):
    """Подтверждение отмены: пуш партнёру один раз, повтор — «уже отменён»."""
    bot = FakeBot()
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    await db.bind_chat(pid, PARTNER_CHAT)
    oid = await db.add_order(pid, 1000, 100)
    await db.set_window(ADMIN_CHAT, 50)

    await order_cancel_confirm(fake_cb(f"ac:ok:{oid}", message_id=50), db, bot, CONFIG)
    assert (await db.get_order(oid))["cancelled_at"] is not None
    pushes = [m for m in bot.sent if m[0] == PARTNER_CHAT]
    assert len(pushes) == 1

    # повторное нажатие: заказ не трогается, второй пуш не уходит
    await order_cancel_confirm(fake_cb(f"ac:ok:{oid}", message_id=50), db, bot, CONFIG)
    assert "уже был отменён" in bot.edited[-1][2]
    assert len([m for m in bot.sent if m[0] == PARTNER_CHAT]) == 1


async def test_pick_cancelled_order_shows_alert(db, password_hash):
    bot = FakeBot()
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    oid = await db.add_order(pid, 1000, 100)
    await db.cancel_order(oid)

    cb = fake_cb(f"ac:o:{oid}", message_id=50)
    await order_cancel_pick(cb, db, bot, CONFIG)
    cb.answer.assert_awaited_with("Заказ уже отменён", show_alert=True)
    assert bot.edited == []


async def test_migration_v2_to_v3_adds_cancelled_at(tmp_path):
    """База v2 (без cancelled_at) получает колонку и новый частичный индекс."""
    path = str(tmp_path / "v2.db")
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
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_orders_unpaid ON orders(partner_id) WHERE payout_id IS NULL;
    """)
    conn.execute(
        "INSERT INTO partners (promo_code, venue_name, password_hash, created_at)"
        " VALUES ('vr101', 'Club X', 'hash', '2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO orders (partner_id, amount, commission, created_at)"
        " VALUES (1, 1000, 100, '2026-01-02T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    db = Database(path)
    await db.init()
    try:
        # старые заказы читаются как неотменённые
        assert (await db.get_order(1))["cancelled_at"] is None
        assert (await db.unpaid_total(1))["orders_count"] == 1
        assert await db.cancel_order(1)

        # частичный индекс пересоздан с предикатом по cancelled_at
        cur = await db.conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'idx_orders_unpaid'"
        )
        index_sql = (await cur.fetchone())["sql"]
        assert "cancelled_at IS NULL" in index_sql
    finally:
        await db.close()
