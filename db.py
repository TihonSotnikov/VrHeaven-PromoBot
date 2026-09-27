"""Слой работы с SQLite через aiosqlite.

Схема:
  partners — партнёры (логин = промокод, пароль хэширован). Промокод
             не уникален глобально: он закреплён только за действующим
             (активным и не удалённым) партнёром, после деактивации или
             удаления высвобождается. Удаление — мягкое (deleted_at),
             история заказов и выплат сохраняется.
  partner_chats — чаты кабинета: у партнёра может быть несколько
             Telegram-аккаунтов (совладельцы заведения), каждый со своим
             чатом. PRIMARY KEY по chat_id гарантирует «один чат — один
             партнёр» — на этом инварианте держится авторизация по чату.
  orders   — заказы; payout_id IS NULL означает «не выплачен». Отмена —
             мягкая (cancelled_at): заказ исключается из всех расчётов,
             но остаётся в истории и экспорте. Текущий период партнёра —
             заказы с payout_id IS NULL AND cancelled_at IS NULL.
  payouts  — выплаты; при выплате невыплаченные заказы партнёра
             привязываются к записи выплаты.
  windows  — идентификатор единственного интерфейсного сообщения в чате.
"""

import aiosqlite

from utils import utcnow_iso

_PARTNERS_DDL = """(
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    promo_code         TEXT    NOT NULL,
    venue_name         TEXT    NOT NULL,
    contact            TEXT    NOT NULL DEFAULT '',
    password_hash      TEXT    NOT NULL,
    commission_percent REAL    NOT NULL DEFAULT 10,
    is_active          INTEGER NOT NULL DEFAULT 1,
    deleted_at         TEXT,
    created_at         TEXT    NOT NULL
)"""

_PARTNER_CHATS_DDL = """(
    chat_id    INTEGER PRIMARY KEY,
    partner_id INTEGER NOT NULL REFERENCES partners(id),
    created_at TEXT    NOT NULL
)"""

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS partners {_PARTNERS_DDL};

CREATE INDEX IF NOT EXISTS idx_partners_code ON partners(promo_code);

CREATE TABLE IF NOT EXISTS partner_chats {_PARTNER_CHATS_DDL};

CREATE INDEX IF NOT EXISTS idx_partner_chats_partner ON partner_chats(partner_id);

CREATE TABLE IF NOT EXISTS payouts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id   INTEGER NOT NULL REFERENCES partners(id),
    amount       REAL    NOT NULL,
    orders_count INTEGER NOT NULL,
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id   INTEGER NOT NULL REFERENCES partners(id),
    amount       REAL    NOT NULL,
    commission   REAL    NOT NULL,
    payout_id    INTEGER REFERENCES payouts(id),
    cancelled_at TEXT,
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_unpaid ON orders(partner_id)
    WHERE payout_id IS NULL AND cancelled_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_payouts_partner ON payouts(partner_id);

CREATE TABLE IF NOT EXISTS windows (
    chat_id    INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self._migrate()
        await self.conn.executescript(SCHEMA)
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.commit()

    async def _columns(self, table: str) -> list[str]:
        cur = await self.conn.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in await cur.fetchall()]

    async def _migrate(self) -> None:
        """Пошаговые миграции старых баз; свежая база сразу получает
        актуальную схему из SCHEMA. Каждый шаг воспроизводит схему своей
        версии дословно — правки актуальной SCHEMA шагов не меняют.

        v1 -> v2: снимает UNIQUE с promo_code, добавляет partners.deleted_at.
        v2 -> v3: добавляет orders.cancelled_at (мягкая отмена заказа);
                  частичный индекс пересоздаётся из SCHEMA с новым предикатом.
        v3 -> v4: выносит привязки чатов в partner_chats (несколько
                  Telegram-аккаунтов у одного партнёра), убирает
                  partners.chat_id.
        """
        partners_columns = await self._columns("partners")
        if not partners_columns:
            return
        if "deleted_at" not in partners_columns:
            await self.conn.executescript("""
                PRAGMA foreign_keys = OFF;
                CREATE TABLE partners_v2 (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    promo_code         TEXT    NOT NULL,
                    venue_name         TEXT    NOT NULL,
                    contact            TEXT    NOT NULL DEFAULT '',
                    password_hash      TEXT    NOT NULL,
                    commission_percent REAL    NOT NULL DEFAULT 10,
                    is_active          INTEGER NOT NULL DEFAULT 1,
                    deleted_at         TEXT,
                    chat_id            INTEGER,
                    created_at         TEXT    NOT NULL
                );
                INSERT INTO partners_v2 (id, promo_code, venue_name, contact, password_hash,
                                         commission_percent, is_active, chat_id, created_at)
                    SELECT id, promo_code, venue_name, contact, password_hash,
                           commission_percent, is_active, chat_id, created_at
                    FROM partners;
                DROP TABLE partners;
                ALTER TABLE partners_v2 RENAME TO partners;
            """)
        orders_columns = await self._columns("orders")
        if orders_columns and "cancelled_at" not in orders_columns:
            await self.conn.executescript("""
                ALTER TABLE orders ADD COLUMN cancelled_at TEXT;
                DROP INDEX IF EXISTS idx_orders_unpaid;
            """)
        if "chat_id" in await self._columns("partners"):
            await self.conn.execute(
                f"CREATE TABLE IF NOT EXISTS partner_chats {_PARTNER_CHATS_DDL}"
            )
            await self.conn.execute(
                "INSERT OR IGNORE INTO partner_chats (chat_id, partner_id, created_at)"
                " SELECT chat_id, id, ? FROM partners"
                " WHERE chat_id IS NOT NULL AND deleted_at IS NULL",
                (utcnow_iso(),),
            )
            await self.conn.executescript("""
                PRAGMA foreign_keys = OFF;
                CREATE TABLE partners_v4 (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    promo_code         TEXT    NOT NULL,
                    venue_name         TEXT    NOT NULL,
                    contact            TEXT    NOT NULL DEFAULT '',
                    password_hash      TEXT    NOT NULL,
                    commission_percent REAL    NOT NULL DEFAULT 10,
                    is_active          INTEGER NOT NULL DEFAULT 1,
                    deleted_at         TEXT,
                    created_at         TEXT    NOT NULL
                );
                INSERT INTO partners_v4 (id, promo_code, venue_name, contact, password_hash,
                                         commission_percent, is_active, deleted_at, created_at)
                    SELECT id, promo_code, venue_name, contact, password_hash,
                           commission_percent, is_active, deleted_at, created_at
                    FROM partners;
                DROP TABLE partners;
                ALTER TABLE partners_v4 RENAME TO partners;
            """)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # ------------------------------------------------------------ Партнёры

    async def create_partner(
        self, promo_code: str, venue_name: str, contact: str,
        password_hash: str, commission_percent: float = 10,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO partners (promo_code, venue_name, contact, password_hash,"
            " commission_percent, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (promo_code, venue_name, contact, password_hash, commission_percent, utcnow_iso()),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_partner(self, partner_id: int) -> aiosqlite.Row | None:
        """Партнёр по id, включая удалённых (нужно для истории и экспорта)."""
        cur = await self.conn.execute("SELECT * FROM partners WHERE id = ?", (partner_id,))
        return await cur.fetchone()

    async def get_partner_by_code(self, promo_code: str) -> aiosqlite.Row | None:
        """Действующий партнёр по промокоду — для входа в кабинет."""
        cur = await self.conn.execute(
            "SELECT * FROM partners WHERE promo_code = ?"
            " AND is_active = 1 AND deleted_at IS NULL",
            (promo_code,),
        )
        return await cur.fetchone()

    async def promo_code_available(self, promo_code: str, exclude_id: int | None = None) -> bool:
        """Свободен ли промокод: занят только действующим партнёром."""
        sql = ("SELECT 1 FROM partners WHERE promo_code = ?"
               " AND is_active = 1 AND deleted_at IS NULL")
        params: list = [promo_code]
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        cur = await self.conn.execute(sql, params)
        return await cur.fetchone() is None

    async def get_partner_by_chat(self, chat_id: int) -> aiosqlite.Row | None:
        """Партнёр по привязанному чату; удалённые теряют доступ."""
        cur = await self.conn.execute(
            "SELECT p.* FROM partners p"
            " JOIN partner_chats c ON c.partner_id = p.id"
            " WHERE c.chat_id = ? AND p.deleted_at IS NULL",
            (chat_id,),
        )
        return await cur.fetchone()

    async def list_partners(self, active_only: bool = False) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM partners WHERE deleted_at IS NULL"
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY promo_code"
        cur = await self.conn.execute(sql)
        return await cur.fetchall()

    async def set_partner_active(self, partner_id: int, is_active: bool) -> None:
        await self.conn.execute(
            "UPDATE partners SET is_active = ? WHERE id = ?", (int(is_active), partner_id)
        )
        await self.conn.commit()

    async def set_partner_password(self, partner_id: int, password_hash: str) -> None:
        await self.conn.execute(
            "UPDATE partners SET password_hash = ? WHERE id = ?", (password_hash, partner_id)
        )
        await self.conn.commit()

    async def set_partner_percent(self, partner_id: int, percent: float) -> None:
        await self.conn.execute(
            "UPDATE partners SET commission_percent = ? WHERE id = ?", (percent, partner_id)
        )
        await self.conn.commit()

    async def delete_partner(self, partner_id: int) -> None:
        """Мягкое удаление: промокод высвобождается, все чаты кабинета
        отвязываются — доступ закрывается на каждом устройстве."""
        await self.conn.execute(
            "UPDATE partners SET deleted_at = ?, is_active = 0 WHERE id = ?",
            (utcnow_iso(), partner_id),
        )
        await self.conn.execute(
            "DELETE FROM partner_chats WHERE partner_id = ?", (partner_id,)
        )
        await self.conn.commit()

    async def bind_chat(self, partner_id: int, chat_id: int) -> None:
        """Привязывает чат к партнёру. Один чат — один партнёр (повторный
        вход переносит только этот чат); чатов у партнёра может быть много."""
        await self.conn.execute(
            "INSERT INTO partner_chats (chat_id, partner_id, created_at)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(chat_id) DO UPDATE SET partner_id = excluded.partner_id,"
            " created_at = excluded.created_at",
            (chat_id, partner_id, utcnow_iso()),
        )
        await self.conn.commit()

    async def chats_for_partner(self, partner_id: int) -> list[int]:
        """Чаты кабинета партнёра — получатели пуш-уведомлений."""
        cur = await self.conn.execute(
            "SELECT chat_id FROM partner_chats WHERE partner_id = ?"
            " ORDER BY created_at, chat_id",
            (partner_id,),
        )
        return [row["chat_id"] for row in await cur.fetchall()]

    async def unbind_partner_chats(self, partner_id: int) -> None:
        """Отвязывает все чаты партнёра: устройства теряют кабинет
        до повторного входа (используется при смене пароля)."""
        await self.conn.execute(
            "DELETE FROM partner_chats WHERE partner_id = ?", (partner_id,)
        )
        await self.conn.commit()

    # -------------------------------------------------------------- Заказы

    async def add_order(self, partner_id: int, amount: float, commission: float) -> int:
        cur = await self.conn.execute(
            "INSERT INTO orders (partner_id, amount, commission, created_at)"
            " VALUES (?, ?, ?, ?)",
            (partner_id, amount, commission, utcnow_iso()),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_order(self, order_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT o.*, p.promo_code, p.venue_name FROM orders o"
            " JOIN partners p ON p.id = o.partner_id WHERE o.id = ?",
            (order_id,),
        )
        return await cur.fetchone()

    async def cancel_order(self, order_id: int) -> bool:
        """Мягкая отмена: заказ исключается из расчётов, но остаётся в истории.

        Возвращает False, если заказ уже отменён (или не существует) —
        защита от двойной отмены при параллельной работе админов.
        """
        cur = await self.conn.execute(
            "UPDATE orders SET cancelled_at = ? WHERE id = ? AND cancelled_at IS NULL",
            (utcnow_iso(), order_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def last_orders(self, limit: int = 10) -> list[aiosqlite.Row]:
        """Последние неотменённые заказы — экран выбора заказа для отмены."""
        cur = await self.conn.execute(
            "SELECT o.*, p.promo_code FROM orders o"
            " JOIN partners p ON p.id = o.partner_id"
            " WHERE o.cancelled_at IS NULL ORDER BY o.id DESC LIMIT ?",
            (limit,),
        )
        return await cur.fetchall()

    async def unpaid_orders(self, partner_id: int) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM orders WHERE partner_id = ?"
            " AND payout_id IS NULL AND cancelled_at IS NULL ORDER BY id",
            (partner_id,),
        )
        return await cur.fetchall()

    async def unpaid_total(self, partner_id: int) -> aiosqlite.Row:
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS orders_count,"
            " COALESCE(SUM(amount), 0) AS turnover,"
            " COALESCE(SUM(commission), 0) AS commission_sum"
            " FROM orders WHERE partner_id = ?"
            " AND payout_id IS NULL AND cancelled_at IS NULL",
            (partner_id,),
        )
        return await cur.fetchone()

    async def unpaid_summary(self) -> list[aiosqlite.Row]:
        """Сводка текущего периода по каждому не удалённому партнёру."""
        cur = await self.conn.execute(
            "SELECT p.id, p.promo_code, p.venue_name, p.is_active,"
            " COUNT(o.id) AS orders_count,"
            " COALESCE(SUM(o.amount), 0) AS turnover,"
            " COALESCE(SUM(o.commission), 0) AS commission_sum"
            " FROM partners p"
            " LEFT JOIN orders o ON o.partner_id = p.id"
            "   AND o.payout_id IS NULL AND o.cancelled_at IS NULL"
            " WHERE p.deleted_at IS NULL"
            " GROUP BY p.id ORDER BY p.promo_code"
        )
        return await cur.fetchall()

    # ------------------------------------------------------------- Выплаты

    async def create_payout(self, partner_id: int) -> tuple[int, float, int] | None:
        """Помечает все невыплаченные заказы партнёра выплаченными.

        Возвращает (id выплаты, сумма, число заказов) или None, если платить нечего.
        """
        total = await self.unpaid_total(partner_id)
        if total["orders_count"] == 0:
            return None
        amount = round(total["commission_sum"], 2)
        cur = await self.conn.execute(
            "INSERT INTO payouts (partner_id, amount, orders_count, created_at)"
            " VALUES (?, ?, ?, ?)",
            (partner_id, amount, total["orders_count"], utcnow_iso()),
        )
        payout_id = cur.lastrowid
        await self.conn.execute(
            "UPDATE orders SET payout_id = ? WHERE partner_id = ?"
            " AND payout_id IS NULL AND cancelled_at IS NULL",
            (payout_id, partner_id),
        )
        await self.conn.commit()
        return payout_id, amount, total["orders_count"]

    async def payouts_for_partner(self, partner_id: int, limit: int = 30) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM payouts WHERE partner_id = ? ORDER BY id DESC LIMIT ?",
            (partner_id, limit),
        )
        return await cur.fetchall()

    # ---------------------------------------------------------------- Окно

    async def get_window(self, chat_id: int) -> int | None:
        cur = await self.conn.execute(
            "SELECT message_id FROM windows WHERE chat_id = ?", (chat_id,)
        )
        row = await cur.fetchone()
        return row["message_id"] if row else None

    async def set_window(self, chat_id: int, message_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO windows (chat_id, message_id) VALUES (?, ?)"
            " ON CONFLICT(chat_id) DO UPDATE SET message_id = excluded.message_id",
            (chat_id, message_id),
        )
        await self.conn.commit()

    # ------------------------------------------------------------- Экспорт

    async def export_partners(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT p.id, p.promo_code, p.venue_name, p.contact, p.commission_percent,"
            " p.is_active, p.deleted_at, p.created_at,"
            " (SELECT COUNT(*) FROM partner_chats c WHERE c.partner_id = p.id)"
            "   AS chats_count"
            " FROM partners p ORDER BY p.id"
        )
        return await cur.fetchall()

    async def export_orders(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT o.id, p.promo_code, o.amount, o.commission, o.payout_id,"
            " o.cancelled_at, o.created_at"
            " FROM orders o JOIN partners p ON p.id = o.partner_id ORDER BY o.id"
        )
        return await cur.fetchall()

    async def export_payouts(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT y.id, p.promo_code, y.amount, y.orders_count, y.created_at"
            " FROM payouts y JOIN partners p ON p.id = y.partner_id ORDER BY y.id"
        )
        return await cur.fetchall()
