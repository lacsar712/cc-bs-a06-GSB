import os

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from rules import judge_microstrain

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54398/bridgestrain"
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS warning_band_versions (
    id serial PRIMARY KEY,
    warn_inner double precision NOT NULL,
    warn_outer double precision NOT NULL,
    changed_by text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now(),
    note text
);

CREATE TABLE IF NOT EXISTS strain_readings (
    id serial PRIMARY KEY,
    span_code text NOT NULL,
    microstrain double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    warning boolean NOT NULL DEFAULT false,
    warn_inner double precision,
    warn_outer double precision,
    band_version_id integer REFERENCES warning_band_versions(id)
);

-- 旧库补列（幂等）
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS warning boolean NOT NULL DEFAULT false;
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS warn_inner double precision;
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS warn_outer double precision;
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS band_version_id integer
    REFERENCES warning_band_versions(id);

CREATE INDEX IF NOT EXISTS idx_strain_readings_status ON strain_readings (status, id);
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


def _seed_insert_sql() -> str:
    return """
        INSERT INTO strain_readings
            (span_code, microstrain, verdict, reason, status, created_by,
             processed_at, warning, warn_inner, warn_outer, band_version_id)
        VALUES (%s, %s, %s, %s, 'done', 'surveyor', now(), %s, NULL, NULL, NULL)
        """


def _seed_params(span_code: str, microstrain: float) -> tuple:
    verdict, reason, warning = judge_microstrain(microstrain, None, None)
    return (span_code, microstrain, verdict, reason, warning)


SEED_SAMPLES = [("跨中S1", 150.0), ("支座S2", 40.0)]


async def seed_if_empty(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) AS n FROM strain_readings")
            row = await cur.fetchone()
            if row["n"] > 0:
                return
            sql = _seed_insert_sql()
            for span_code, microstrain in SEED_SAMPLES:
                await cur.execute(sql, _seed_params(span_code, microstrain))
        await conn.commit()


def connect_sync():
    import psycopg

    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)
    conn.commit()


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM strain_readings").fetchone()
    if row["n"] > 0:
        return
    sql = _seed_insert_sql()
    for span_code, microstrain in SEED_SAMPLES:
        conn.execute(sql, _seed_params(span_code, microstrain))
    conn.commit()


# ---------------------------------------------------------------------------
# 警戒黄带：当前生效版本与改档流水
# ---------------------------------------------------------------------------

ACTIVE_BAND_SQL = (
    "SELECT * FROM warning_band_versions ORDER BY id DESC LIMIT 1"
)


def get_active_band_sync(conn) -> dict | None:
    """工人领单时调用：取当前最新黄带版本（改档前的旧单不受影响）。"""
    return conn.execute(ACTIVE_BAND_SQL).fetchone()


async def get_active_band(pool: AsyncConnectionPool) -> dict | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(ACTIVE_BAND_SQL)
            return await cur.fetchone()


async def list_band_versions(pool: AsyncConnectionPool, limit: int = 100) -> list[dict]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM warning_band_versions ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            return await cur.fetchall()


async def insert_band_version(
    pool: AsyncConnectionPool,
    warn_inner: float,
    warn_outer: float,
    changed_by: str,
    note: str | None = None,
) -> dict:
    """改档：写入新版本。只影响写入之后被领走的新单。"""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO warning_band_versions
                    (warn_inner, warn_outer, changed_by, note)
                VALUES (%s, %s, %s, %s)
                RETURNING *
                """,
                (warn_inner, warn_outer, changed_by, note),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row
