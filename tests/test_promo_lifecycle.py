"""Высвобождение промокода при деактивации и удалении партнёра."""

import sqlite3

from db import Database


async def test_code_taken_by_active_partner(db, password_hash):
    await db.create_partner("vr101", "Club X", "", password_hash)
    assert not await db.promo_code_available("vr101")


async def test_deactivation_frees_code(db, password_hash):
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    await db.set_partner_active(pid, False)
    assert await db.promo_code_available("vr101")


async def test_deletion_frees_code_and_allows_reregistration(db, password_hash):
    old_id = await db.create_partner("vr101", "Club X", "", password_hash)
    await db.add_order(old_id, 1000, 100)
    await db.delete_partner(old_id)

    assert await db.promo_code_available("vr101")
    new_id = await db.create_partner("vr101", "Club Y", "", password_hash)
    assert new_id != old_id

    # вход по промокоду ведёт на нового действующего партнёра
    found = await db.get_partner_by_code("vr101")
    assert found["id"] == new_id

    # история удалённого партнёра сохраняется для отчётности
    old = await db.get_partner(old_id)
    assert old is not None and old["deleted_at"] is not None
    assert len(await db.export_orders()) == 1


async def test_deleted_partner_loses_chat_access(db, password_hash):
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    await db.bind_chat(pid, 777)
    assert (await db.get_partner_by_chat(777))["id"] == pid

    await db.delete_partner(pid)
    # привязка чата аннулирована: интерфейс блокируется при следующем нажатии
    assert await db.get_partner_by_chat(777) is None


async def test_deleted_partner_excluded_from_summary_and_lists(db, password_hash):
    pid = await db.create_partner("vr101", "Club X", "", password_hash)
    await db.add_order(pid, 1000, 100)
    await db.delete_partner(pid)

    assert await db.unpaid_summary() == []
    assert await db.list_partners() == []
    # но в экспорте партнёр остаётся с отметкой об удалении
    exported = await db.export_partners()
    assert len(exported) == 1 and exported[0]["deleted_at"] is not None


async def test_reactivation_blocked_when_code_reassigned(db, password_hash):
    old_id = await db.create_partner("vr101", "Club X", "", password_hash)
    await db.set_partner_active(old_id, False)
    await db.create_partner("vr101", "Club Y", "", password_hash)

    # код закреплён за новым действующим партнёром — старого активировать нельзя
    assert not await db.promo_code_available("vr101", exclude_id=old_id)


async def test_migration_from_v1_schema(tmp_path, password_hash):
    """Старая база (promo_code UNIQUE, без deleted_at) мигрирует без потери данных."""
    path = str(tmp_path / "v1.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE partners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_code TEXT NOT NULL UNIQUE,
            venue_name TEXT NOT NULL,
            contact TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            commission_percent REAL NOT NULL DEFAULT 10,
            is_active INTEGER NOT NULL DEFAULT 1,
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
    """)
    conn.execute(
        "INSERT INTO partners (promo_code, venue_name, contact, password_hash,"
        " commission_percent, is_active, chat_id, created_at)"
        " VALUES ('vr101', 'Club X', '', 'hash', 10, 1, 777, '2026-01-01T00:00:00+00:00')"
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
        partner = await db.get_partner_by_code("vr101")
        assert partner is not None and partner["deleted_at"] is None
        # привязка чата переехала в partner_chats без потери
        assert (await db.get_partner_by_chat(777))["id"] == partner["id"]
        assert await db.chats_for_partner(partner["id"]) == [777]
        assert (await db.get_order(1))["promo_code"] == "vr101"

        # UNIQUE снят: после удаления код можно выдать заново
        await db.delete_partner(partner["id"])
        await db.create_partner("vr101", "Club Y", "", password_hash)
        assert (await db.get_partner_by_code("vr101"))["venue_name"] == "Club Y"
    finally:
        await db.close()
