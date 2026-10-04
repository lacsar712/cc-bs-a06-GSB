"""后台工人：用 SKIP LOCKED 认领 pending 应变读数。

领单的同一事务内抄下当时最新的警戒黄带边界（内外沿 + 版本号）写入单据，
随后判定一律以这份快照为准。因此改档只影响改档之后被领走的新单，
已被领走的单据继续沿用领单瞬间抄下的边界。
"""

import os
import time

from db import (
    connect_sync,
    ensure_schema_sync,
    get_active_band_sync,
    seed_if_empty_sync,
)
from rules import judge_microstrain

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "1.0"))


def claim_one(conn):
    with conn.transaction():
        row = conn.execute(
            """
            SELECT id, microstrain
            FROM strain_readings
            WHERE status = 'pending'
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None

        # 领单同一事务内抄下当前黄带边界快照
        band = get_active_band_sync(conn)
        warn_inner = band["warn_inner"] if band else None
        warn_outer = band["warn_outer"] if band else None
        band_version_id = band["id"] if band else None
        conn.execute(
            """
            UPDATE strain_readings
            SET status = 'processing',
                warn_inner = %s,
                warn_outer = %s,
                band_version_id = %s
            WHERE id = %s
            """,
            (warn_inner, warn_outer, band_version_id, row["id"]),
        )

        row = dict(row)
        row["warn_inner"] = warn_inner
        row["warn_outer"] = warn_outer
        row["band_version_id"] = band_version_id
        return row


def finish(conn, reading_id: int, microstrain: float, warn_inner, warn_outer) -> None:
    verdict, reason, warning = judge_microstrain(
        microstrain, warn_inner, warn_outer
    )
    conn.execute(
        """
        UPDATE strain_readings
        SET status = 'done', verdict = %s, reason = %s, warning = %s,
            processed_at = now()
        WHERE id = %s
        """,
        (verdict, reason, warning, reading_id),
    )
    conn.commit()


def run_once(conn) -> bool:
    row = claim_one(conn)
    if not row:
        return False
    try:
        finish(
            conn,
            row["id"],
            float(row["microstrain"]),
            row["warn_inner"],
            row["warn_outer"],
        )
    except Exception:
        # 处理失败：回队并清空快照，下次领取时按当时最新边界重新抄档
        conn.execute(
            """
            UPDATE strain_readings
            SET status = 'pending',
                warn_inner = NULL,
                warn_outer = NULL,
                band_version_id = NULL
            WHERE id = %s
            """,
            (row["id"],),
        )
        conn.commit()
        raise
    return True


def main() -> None:
    with connect_sync() as conn:
        ensure_schema_sync(conn)
        seed_if_empty_sync(conn)
        conn.commit()

    while True:
        try:
            with connect_sync() as conn:
                processed = run_once(conn)
        except Exception as exc:
            print(f"worker error: {exc}", flush=True)
            processed = False
        if not processed:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
