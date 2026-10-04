import os

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from rules import (
    DEFAULT_BAND_ENABLED,
    DEFAULT_BAND_INNER,
    DEFAULT_BAND_OUTER,
    PASS_HIGH,
    PASS_LOW,
    judge_microstrain,
)

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54398/bridgestrain"
)

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS strain_readings (
    id serial PRIMARY KEY,
    span_code text NOT NULL,
    microstrain double precision NOT NULL,
    verdict text,
    warning boolean NOT NULL DEFAULT false,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    -- 领单瞬间抄下的黄带边界（快照）；pending 行为空，认领时写入，判定永远用它。
    band_enabled boolean,
    band_inner double precision,
    band_outer double precision
);
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS warning boolean NOT NULL DEFAULT false;
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS band_enabled boolean;
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS band_inner double precision;
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS band_outer double precision;
CREATE INDEX IF NOT EXISTS idx_strain_readings_status ON strain_readings (status, id);

-- 黄带当前生效配置：始终只有 id=1 一行。
CREATE TABLE IF NOT EXISTS warning_band_config (
    id smallint PRIMARY KEY DEFAULT 1,
    enabled boolean NOT NULL DEFAULT true,
    inner_edge double precision NOT NULL DEFAULT {DEFAULT_BAND_INNER},
    outer_edge double precision NOT NULL DEFAULT {DEFAULT_BAND_OUTER},
    updated_by text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT warning_band_singleton CHECK (id = 1),
    CONSTRAINT warning_band_edges_ordered CHECK (inner_edge <= outer_edge),
    CONSTRAINT warning_band_inside_pass CHECK (
        inner_edge >= {PASS_LOW} AND outer_edge <= {PASS_HIGH}
    )
);

-- 黄带改档流水：每次改档追加一行，只增不改。
CREATE TABLE IF NOT EXISTS warning_band_history (
    id serial PRIMARY KEY,
    enabled boolean NOT NULL,
    inner_edge double precision NOT NULL,
    outer_edge double precision NOT NULL,
    changed_by text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now(),
    note text
);
"""


async def create_pool() -> AsyncConnectionPool:
    pool = AsyncConnectionPool(
        conninfo=DSN,
        min_size=1,
        max_size=5,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    await pool.open()
    return pool


async def ensure_schema(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        await conn.execute(SCHEMA_SQL)
        await conn.commit()


async def seed_if_empty(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await _ensure_default_config_async(cur)
            await cur.execute("SELECT COUNT(*) AS n FROM strain_readings")
            row = await cur.fetchone()
            if row["n"] == 0:
                await _seed_samples(cur)
        await conn.commit()


def connect_sync():
    import psycopg

    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


def seed_if_empty_sync(conn) -> None:
    with conn.cursor() as cur:
        _ensure_default_config_sync(cur)
        row = cur.execute("SELECT COUNT(*) AS n FROM strain_readings").fetchone()
        if row["n"] == 0:
            _seed_samples_sync(cur)
    conn.commit()


# --------------------------------------------------------------------------- #
# 黄带配置访问（同步/异步各一份；worker 与 API 都读同一行同一套边界口径）
# --------------------------------------------------------------------------- #
def get_band_config_sync(conn) -> dict:
    """读取当前生效黄带配置；单行表不存在时兜底默认值。"""
    row = conn.execute(
        """
        SELECT enabled, inner_edge, outer_edge, updated_by, updated_at
        FROM warning_band_config WHERE id = 1
        """
    ).fetchone()
    if row is None:
        return {
            "enabled": DEFAULT_BAND_ENABLED,
            "inner_edge": DEFAULT_BAND_INNER,
            "outer_edge": DEFAULT_BAND_OUTER,
            "updated_by": None,
            "updated_at": None,
        }
    return _config_row_to_dict(row)


async def get_band_config_async(cur) -> dict:
    await cur.execute(
        """
        SELECT enabled, inner_edge, outer_edge, updated_by, updated_at
        FROM warning_band_config WHERE id = 1
        """
    )
    row = await cur.fetchone()
    if row is None:
        return {
            "enabled": DEFAULT_BAND_ENABLED,
            "inner_edge": DEFAULT_BAND_INNER,
            "outer_edge": DEFAULT_BAND_OUTER,
            "updated_by": None,
            "updated_at": None,
        }
    return _config_row_to_dict(row)


async def update_band_config_async(
    cur, *, enabled: bool, inner_edge: float, outer_edge: float,
    changed_by: str, note: str | None = None,
) -> dict:
    """改档（异步）：更新单行配置并向流水追加一行，随调用方事务提交。"""
    await cur.execute(
        """
        INSERT INTO warning_band_config AS c
            (id, enabled, inner_edge, outer_edge, updated_by, updated_at)
        VALUES (1, %s, %s, %s, %s, now())
        ON CONFLICT (id) DO UPDATE
            SET enabled = EXCLUDED.enabled,
                inner_edge = EXCLUDED.inner_edge,
                outer_edge = EXCLUDED.outer_edge,
                updated_by = EXCLUDED.updated_by,
                updated_at = now()
        RETURNING enabled, inner_edge, outer_edge, updated_by, updated_at
        """,
        (enabled, inner_edge, outer_edge, changed_by),
    )
    row = await cur.fetchone()
    await cur.execute(
        """
        INSERT INTO warning_band_history
            (enabled, inner_edge, outer_edge, changed_by, changed_at, note)
        VALUES (%s, %s, %s, %s, now(), %s)
        """,
        (enabled, inner_edge, outer_edge, changed_by, note),
    )
    return _config_row_to_dict(row)


def _config_row_to_dict(row) -> dict:
    return {
        "enabled": row["enabled"],
        "inner_edge": float(row["inner_edge"]),
        "outer_edge": float(row["outer_edge"]),
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


def _ensure_default_config(cur) -> None:
    cur.execute(
        """
        INSERT INTO warning_band_config (id, enabled, inner_edge, outer_edge, updated_at)
        VALUES (1, %s, %s, %s, now())
        ON CONFLICT (id) DO NOTHING
        """,
        (DEFAULT_BAND_ENABLED, DEFAULT_BAND_INNER, DEFAULT_BAND_OUTER),
    )


async def _ensure_default_config_async(cur) -> None:
    await cur.execute(
        """
        INSERT INTO warning_band_config (id, enabled, inner_edge, outer_edge, updated_at)
        VALUES (1, %s, %s, %s, now())
        ON CONFLICT (id) DO NOTHING
        """,
        (DEFAULT_BAND_ENABLED, DEFAULT_BAND_INNER, DEFAULT_BAND_OUTER),
    )


_ensure_default_config_sync = _ensure_default_config


# --------------------------------------------------------------------------- #
# 种子读数
# --------------------------------------------------------------------------- #
async def _seed_samples(cur) -> None:
    for span_code, microstrain in [("跨中S1", 150.0), ("支座S2", 40.0)]:
        verdict, warning, reason = judge_microstrain(microstrain)
        await cur.execute(
            """
            INSERT INTO strain_readings
                (span_code, microstrain, verdict, warning, reason, status,
                 created_by, processed_at, band_enabled, band_inner, band_outer)
            VALUES (%s, %s, %s, %s, %s, 'done', 'surveyor', now(), %s, %s, %s)
            """,
            (
                span_code,
                microstrain,
                verdict,
                warning,
                reason,
                DEFAULT_BAND_ENABLED,
                DEFAULT_BAND_INNER,
                DEFAULT_BAND_OUTER,
            ),
        )


def _seed_samples_sync(cur) -> None:
    for span_code, microstrain in [("跨中S1", 150.0), ("支座S2", 40.0)]:
        verdict, warning, reason = judge_microstrain(microstrain)
        cur.execute(
            """
            INSERT INTO strain_readings
                (span_code, microstrain, verdict, warning, reason, status,
                 created_by, processed_at, band_enabled, band_inner, band_outer)
            VALUES (%s, %s, %s, %s, %s, 'done', 'surveyor', now(), %s, %s, %s)
            """,
            (
                span_code,
                microstrain,
                verdict,
                warning,
                reason,
                DEFAULT_BAND_ENABLED,
                DEFAULT_BAND_INNER,
                DEFAULT_BAND_OUTER,
            ),
        )
