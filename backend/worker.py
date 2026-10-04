"""后台工人：用 SKIP LOCKED 认领 pending 应变读数并写入合格/越界结论。

关键约定：领单（FOR UPDATE SKIP LOCKED 并置 processing）与抄录当前黄带边界
在同一事务内完成——读数行从此带着自己的黄带快照走，判定只用这套快照。
因此测量员之后改档，只影响改档之后才被认领的新单；已经领走的单据继续按
领单瞬间抄下的边界判定，详情脚注也展示同一套边界，不会“改档翻案”。
"""

import os
import time

from db import (
    connect_sync,
    ensure_schema_sync,
    get_band_config_sync,
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

        # 领单瞬间抄下当前黄带边界，随单据冻结。
        band = get_band_config_sync(conn)
        conn.execute(
            """
            UPDATE strain_readings
            SET status = 'processing',
                band_enabled = %s,
                band_inner = %s,
                band_outer = %s
            WHERE id = %s
            """,
            (
                band["enabled"],
                band["inner_edge"],
                band["outer_edge"],
                row["id"],
            ),
        )
        return {
            "id": row["id"],
            "microstrain": row["microstrain"],
            "band_enabled": band["enabled"],
            "band_inner": band["inner_edge"],
            "band_outer": band["outer_edge"],
        }


def finish(conn, claimed) -> None:
    # 永远用领单瞬间抄下的快照边界判定，不读当前配置。
    verdict, warning, reason = judge_microstrain(
        float(claimed["microstrain"]),
        band_enabled=claimed["band_enabled"],
        band_inner=claimed["band_inner"],
        band_outer=claimed["band_outer"],
    )
    conn.execute(
        """
        UPDATE strain_readings
        SET status = 'done', verdict = %s, warning = %s, reason = %s,
            processed_at = now()
        WHERE id = %s
        """,
        (verdict, warning, reason, claimed["id"]),
    )
    conn.commit()


def run_once(conn) -> bool:
    row = claim_one(conn)
    if not row:
        return False
    try:
        finish(conn, row)
    except Exception:
        conn.execute(
            """
            UPDATE strain_readings
            SET status = 'pending',
                band_enabled = NULL,
                band_inner = NULL,
                band_outer = NULL
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
