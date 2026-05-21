from __future__ import annotations

from decimal import Decimal
from typing import Any

import asyncpg

from app.config import Settings


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    username TEXT,
    full_name TEXT NOT NULL DEFAULT '',
    balance NUMERIC(12, 2) NOT NULL DEFAULT 0,
    purchased_stars BIGINT NOT NULL DEFAULT 0,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    stars BIGINT NOT NULL UNIQUE,
    price_rub NUMERIC(12, 2) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    amount_rub NUMERIC(12, 2) NOT NULL CHECK (amount_rub > 0),
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    paid_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS buyers (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    stars BIGINT NOT NULL CHECK (stars > 0),
    amount_rub NUMERIC(12, 2) NOT NULL CHECK (amount_rub >= 0),
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS crypto_orders (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    invoice_id BIGINT NOT NULL UNIQUE,
    product_type TEXT NOT NULL DEFAULT 'stars',
    stars BIGINT NOT NULL DEFAULT 0,
    premium_months INTEGER,
    amount_rub NUMERIC(12, 2) NOT NULL CHECK (amount_rub > 0),
    pay_url TEXT NOT NULL DEFAULT '',
    recipient_username TEXT,
    recipient_hash TEXT,
    istar_order_id TEXT,
    istar_status TEXT,
    istar_error TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    paid_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS crypto_topups (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    invoice_id BIGINT NOT NULL UNIQUE,
    amount_rub NUMERIC(12, 2) NOT NULL CHECK (amount_rub > 0),
    pay_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    paid_at TIMESTAMPTZ
);

ALTER TABLE crypto_orders ADD COLUMN IF NOT EXISTS recipient_username TEXT;
ALTER TABLE crypto_orders ADD COLUMN IF NOT EXISTS recipient_hash TEXT;
ALTER TABLE crypto_orders ADD COLUMN IF NOT EXISTS istar_order_id TEXT;
ALTER TABLE crypto_orders ADD COLUMN IF NOT EXISTS istar_status TEXT;
ALTER TABLE crypto_orders ADD COLUMN IF NOT EXISTS istar_error TEXT;
ALTER TABLE crypto_orders ADD COLUMN IF NOT EXISTS product_type TEXT NOT NULL DEFAULT 'stars';
ALTER TABLE crypto_orders ADD COLUMN IF NOT EXISTS premium_months INTEGER;
ALTER TABLE crypto_orders ALTER COLUMN stars SET DEFAULT 0;
ALTER TABLE crypto_orders DROP CONSTRAINT IF EXISTS crypto_orders_stars_check;
ALTER TABLE crypto_orders DROP CONSTRAINT IF EXISTS crypto_orders_product_check;
ALTER TABLE crypto_orders ADD CONSTRAINT crypto_orders_product_check
    CHECK (
        (product_type = 'stars' AND stars > 0 AND premium_months IS NULL)
        OR (product_type = 'premium' AND stars = 0 AND premium_months IN (3, 6, 12))
    );
"""


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.settings.database_url)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def init_schema(self) -> None:
        async with self._pool().acquire() as conn:
            await conn.execute(SCHEMA_SQL)
            await self._seed_products(conn)

    async def ensure_user(self, telegram_id: int, username: str | None, full_name: str, is_admin: bool) -> asyncpg.Record:
        return await self._pool().fetchrow(
            """
            INSERT INTO users (telegram_id, username, full_name, is_admin)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (telegram_id) DO UPDATE SET
                username = EXCLUDED.username,
                full_name = EXCLUDED.full_name,
                is_admin = EXCLUDED.is_admin,
                updated_at = NOW()
            RETURNING *
            """,
            telegram_id,
            username,
            full_name,
            is_admin,
        )

    async def get_user(self, telegram_id: int) -> asyncpg.Record | None:
        return await self._pool().fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)

    async def create_payment(self, user_id: int, amount: Decimal) -> asyncpg.Record:
        return await self._pool().fetchrow(
            """
            INSERT INTO payments (user_id, amount_rub)
            VALUES ($1, $2)
            RETURNING *
            """,
            user_id,
            amount,
        )

    async def list_pending_payments(self, limit: int = 10) -> list[asyncpg.Record]:
        return await self._pool().fetch(
            """
            SELECT p.*, u.username, u.full_name
            FROM payments p
            JOIN users u ON u.telegram_id = p.user_id
            WHERE p.status = 'pending'
            ORDER BY p.created_at
            LIMIT $1
            """,
            limit,
        )

    async def set_payment_status(self, payment_id: int, status: str) -> asyncpg.Record | None:
        if status not in {"paid", "cancelled"}:
            raise ValueError("Unsupported payment status")

        async with self._pool().acquire() as conn:
            async with conn.transaction():
                payment = await conn.fetchrow(
                    "SELECT * FROM payments WHERE id = $1 AND status = 'pending' FOR UPDATE",
                    payment_id,
                )
                if not payment:
                    return None

                if status == "paid":
                    await conn.execute(
                        """
                        UPDATE users
                        SET balance = balance + $1, updated_at = NOW()
                        WHERE telegram_id = $2
                        """,
                        payment["amount_rub"],
                        payment["user_id"],
                    )

                return await conn.fetchrow(
                    """
                    UPDATE payments
                    SET status = $2, paid_at = CASE WHEN $2 = 'paid' THEN NOW() ELSE paid_at END
                    WHERE id = $1
                    RETURNING *
                    """,
                    payment_id,
                    status,
                )

    async def get_products(self) -> list[asyncpg.Record]:
        return await self._pool().fetch("SELECT * FROM products WHERE is_active ORDER BY stars")

    async def get_product_by_stars(self, stars: int) -> asyncpg.Record | None:
        return await self._pool().fetchrow(
            "SELECT * FROM products WHERE stars = $1 AND is_active",
            stars,
        )

    async def buy_stars(
        self,
        user_id: int,
        stars: int,
        amount: Decimal,
        product_id: int | None = None,
    ) -> tuple[bool, asyncpg.Record | None]:
        async with self._pool().acquire() as conn:
            async with conn.transaction():
                user = await conn.fetchrow(
                    "SELECT * FROM users WHERE telegram_id = $1 FOR UPDATE",
                    user_id,
                )
                if not user or user["balance"] < amount:
                    return False, user

                await conn.execute(
                    """
                    UPDATE users
                    SET balance = balance - $1,
                        purchased_stars = purchased_stars + $2,
                        updated_at = NOW()
                    WHERE telegram_id = $3
                    """,
                    amount,
                    stars,
                    user_id,
                )
                await conn.execute(
                    """
                    INSERT INTO buyers (user_id, product_id, stars, amount_rub)
                    VALUES ($1, $2, $3, $4)
                    """,
                    user_id,
                    product_id,
                    stars,
                    amount,
                )
                updated_user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", user_id)
                return True, updated_user

    async def create_crypto_order(
        self,
        user_id: int,
        invoice_id: int,
        stars: int,
        amount: Decimal,
        pay_url: str,
    ) -> asyncpg.Record:
        return await self._pool().fetchrow(
            """
            INSERT INTO crypto_orders (user_id, invoice_id, stars, amount_rub, pay_url)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (invoice_id) DO UPDATE SET
                pay_url = EXCLUDED.pay_url
            RETURNING *
            """,
            user_id,
            invoice_id,
            stars,
            amount,
            pay_url,
        )

    async def create_crypto_premium_order(
        self,
        user_id: int,
        invoice_id: int,
        months: int,
        amount: Decimal,
        pay_url: str,
    ) -> asyncpg.Record:
        return await self._pool().fetchrow(
            """
            INSERT INTO crypto_orders (user_id, invoice_id, product_type, stars, premium_months, amount_rub, pay_url)
            VALUES ($1, $2, 'premium', 0, $3, $4, $5)
            ON CONFLICT (invoice_id) DO UPDATE SET
                pay_url = EXCLUDED.pay_url
            RETURNING *
            """,
            user_id,
            invoice_id,
            months,
            amount,
            pay_url,
        )

    async def get_crypto_order(self, invoice_id: int) -> asyncpg.Record | None:
        return await self._pool().fetchrow(
            "SELECT * FROM crypto_orders WHERE invoice_id = $1",
            invoice_id,
        )

    async def get_latest_unsubmitted_paid_crypto_order(self, user_id: int) -> asyncpg.Record | None:
        return await self._pool().fetchrow(
            """
            SELECT *
            FROM crypto_orders
            WHERE user_id = $1
              AND status = 'paid'
              AND istar_order_id IS NULL
            ORDER BY paid_at DESC NULLS LAST, created_at DESC
            LIMIT 1
            """,
            user_id,
        )

    async def complete_crypto_order(self, invoice_id: int) -> tuple[bool, asyncpg.Record | None]:
        async with self._pool().acquire() as conn:
            async with conn.transaction():
                order = await conn.fetchrow(
                    "SELECT * FROM crypto_orders WHERE invoice_id = $1 FOR UPDATE",
                    invoice_id,
                )
                if not order:
                    return False, None
                if order["status"] == "paid":
                    user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", order["user_id"])
                    return False, user

                await conn.execute(
                    """
                    UPDATE crypto_orders
                    SET status = 'paid', paid_at = NOW()
                    WHERE invoice_id = $1
                    """,
                    invoice_id,
                )
                user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", order["user_id"])
                return True, user

    async def set_crypto_order_istar_submitted(
        self,
        invoice_id: int,
        recipient_username: str,
        recipient_hash: str,
        istar_order_id: str,
        istar_status: str,
    ) -> asyncpg.Record | None:
        return await self._pool().fetchrow(
            """
            UPDATE crypto_orders
            SET recipient_username = $2,
                recipient_hash = $3,
                istar_order_id = $4,
                istar_status = $5,
                istar_error = NULL
            WHERE invoice_id = $1
            RETURNING *
            """,
            invoice_id,
            recipient_username,
            recipient_hash,
            istar_order_id,
            istar_status,
        )

    async def set_crypto_order_istar_error(self, invoice_id: int, error: str) -> None:
        await self._pool().execute(
            """
            UPDATE crypto_orders
            SET istar_status = 'failed',
                istar_error = $2
            WHERE invoice_id = $1
            """,
            invoice_id,
            error[:1000],
        )

    async def update_crypto_order_istar_status(
        self,
        istar_order_id: str,
        status: str,
        error: str | None = None,
    ) -> asyncpg.Record | None:
        async with self._pool().acquire() as conn:
            async with conn.transaction():
                order = await conn.fetchrow(
                    "SELECT * FROM crypto_orders WHERE istar_order_id = $1 FOR UPDATE",
                    istar_order_id,
                )
                if not order:
                    return None

                was_completed = order["istar_status"] == "completed"
                updated_order = await conn.fetchrow(
                    """
                    UPDATE crypto_orders
                    SET istar_status = $2,
                        istar_error = $3
                    WHERE istar_order_id = $1
                    RETURNING *
                    """,
                    istar_order_id,
                    status,
                    error[:1000] if error else None,
                )

                if status == "completed" and not was_completed and order["product_type"] == "stars":
                    await conn.execute(
                        """
                        UPDATE users
                        SET purchased_stars = purchased_stars + $1,
                            updated_at = NOW()
                        WHERE telegram_id = $2
                        """,
                        order["stars"],
                        order["user_id"],
                    )
                    await conn.execute(
                        """
                        INSERT INTO buyers (user_id, product_id, stars, amount_rub)
                        VALUES ($1, NULL, $2, $3)
                        """,
                        order["user_id"],
                        order["stars"],
                        order["amount_rub"],
                    )
                return updated_order

    async def create_crypto_topup(
        self,
        user_id: int,
        invoice_id: int,
        amount: Decimal,
        pay_url: str,
    ) -> asyncpg.Record:
        return await self._pool().fetchrow(
            """
            INSERT INTO crypto_topups (user_id, invoice_id, amount_rub, pay_url)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (invoice_id) DO UPDATE SET
                pay_url = EXCLUDED.pay_url
            RETURNING *
            """,
            user_id,
            invoice_id,
            amount,
            pay_url,
        )

    async def get_crypto_topup(self, invoice_id: int) -> asyncpg.Record | None:
        return await self._pool().fetchrow(
            "SELECT * FROM crypto_topups WHERE invoice_id = $1",
            invoice_id,
        )

    async def complete_crypto_topup(self, invoice_id: int) -> tuple[bool, asyncpg.Record | None]:
        async with self._pool().acquire() as conn:
            async with conn.transaction():
                topup = await conn.fetchrow(
                    "SELECT * FROM crypto_topups WHERE invoice_id = $1 FOR UPDATE",
                    invoice_id,
                )
                if not topup:
                    return False, None
                user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1 FOR UPDATE", topup["user_id"])
                if topup["status"] == "paid":
                    return False, user

                await conn.execute(
                    """
                    UPDATE crypto_topups
                    SET status = 'paid', paid_at = NOW()
                    WHERE invoice_id = $1
                    """,
                    invoice_id,
                )
                await conn.execute(
                    """
                    UPDATE users
                    SET balance = balance + $1,
                        updated_at = NOW()
                    WHERE telegram_id = $2
                    """,
                    topup["amount_rub"],
                    topup["user_id"],
                )
                await conn.execute(
                    """
                    INSERT INTO payments (user_id, amount_rub, status, paid_at)
                    VALUES ($1, $2, 'paid', NOW())
                    """,
                    topup["user_id"],
                    topup["amount_rub"],
                )
                user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", topup["user_id"])
                return True, user

    async def stats(self) -> dict[str, Any]:
        row = await self._pool().fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM users) AS users_count,
                (SELECT COALESCE(SUM(amount_rub), 0) FROM payments WHERE status = 'paid') AS paid_amount,
                (SELECT COALESCE(SUM(stars), 0) FROM buyers WHERE status = 'completed') AS sold_stars,
                (SELECT COALESCE(SUM(amount_rub), 0) FROM buyers WHERE status = 'completed') AS sold_amount,
                (SELECT COUNT(*) FROM payments WHERE status = 'pending') AS pending_payments
            """
        )
        return dict(row)

    async def user_purchase_stats(self, user_id: int) -> dict[str, Any]:
        row = await self._pool().fetchrow(
            """
            SELECT
                COUNT(*) AS orders_count,
                COALESCE(SUM(amount_rub), 0) AS total_spent
            FROM buyers
            WHERE user_id = $1 AND status = 'completed'
            """,
            user_id,
        )
        return dict(row)

    async def _seed_products(self, conn: asyncpg.Connection) -> None:
        price = Decimal(str(self.settings.price_per_star_rub))
        products = [
            ("1000 звезд", 1000, price * Decimal(1000)),
            ("10000 звезд", 10000, price * Decimal(10000)),
        ]
        await conn.executemany(
            """
            INSERT INTO products (title, stars, price_rub)
            VALUES ($1, $2, $3)
            ON CONFLICT (stars) DO UPDATE SET
                title = EXCLUDED.title,
                price_rub = EXCLUDED.price_rub,
                is_active = TRUE
            """,
            products,
        )

    def _pool(self) -> asyncpg.Pool:
        if not self.pool:
            raise RuntimeError("Database pool is not initialized")
        return self.pool
