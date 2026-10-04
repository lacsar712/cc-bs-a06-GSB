"""worker 快照语义测试（无需数据库）：python test_worker_snapshot.py

证明：
1. 领单（claim_one）在同一事务内把“当前黄带边界”抄进单据；
2. 判定（finish）只用单据上的快照；
3. 之后黄带改档，只影响改档后新认领的单据，已领单据结论不翻案。
"""

import sys
import types

# worker 仅在连接建立时才用 psycopg；claim/finish 不需要。本地无依赖时打桩，
# 以便零数据库验证快照语义。
if "psycopg" not in sys.modules:
    psycopg = types.ModuleType("psycopg")
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = lambda: None
    psycopg.rows = rows
    pool_pkg = types.ModuleType("psycopg_pool")

    class AsyncConnectionPool:  # 测试不会实例化
        pass

    pool_pkg.AsyncConnectionPool = AsyncConnectionPool
    sys.modules["psycopg"] = psycopg
    sys.modules["psycopg.rows"] = rows
    sys.modules["psycopg_pool"] = pool_pkg

import worker


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Tx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    """最小假连接：按 SQL 片段路由到内存里的待领读数与黄带配置。"""

    def __init__(self, reading, band):
        self._reading = reading
        self._band = band
        self.finished = {}  # reading_id -> (verdict, warning, reason)

    def transaction(self):
        return _Tx()

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT id, microstrain"):
            return _Result(self._reading)
        if s.startswith("SELECT enabled, inner_edge, outer_edge"):
            return _Result(self._band)
        if "SET status = 'processing'" in s:
            return _Result(None)  # 抄快照的 UPDATE
        if "SET status = 'done'" in s:
            verdict, warning, reason, reading_id = params
            self.finished[reading_id] = (verdict, warning, reason)
            return _Result(None)
        return _Result(None)

    def commit(self):
        pass


def band(inner, outer, enabled=True):
    return {
        "enabled": enabled,
        "inner_edge": inner,
        "outer_edge": outer,
        "updated_by": "surveyor",
        "updated_at": None,
    }


def main() -> None:
    # —— 第一批：当前黄带 200～220，读数 210 被认领并判定 ——
    conn1 = FakeConn(
        {"id": 1, "microstrain": 210.0},
        band(200.0, 220.0),
    )
    claimed1 = worker.claim_one(conn1)
    assert claimed1["band_inner"] == 200.0
    assert claimed1["band_outer"] == 220.0
    worker.finish(conn1, claimed1)
    assert conn1.finished[1] == (
        "合格",
        True,
        conn1.finished[1][2],
    ), conn1.finished[1]

    # —— 测量员改档为 205～215 —— 只应影响之后新认领的单据 ——
    # 新单据 216：按新边界，越过外沿 215 -> 越界。
    conn2 = FakeConn(
        {"id": 2, "microstrain": 216.0},
        band(205.0, 215.0),
    )
    claimed2 = worker.claim_one(conn2)
    assert claimed2["band_inner"] == 205.0
    assert claimed2["band_outer"] == 215.0
    worker.finish(conn2, claimed2)
    assert conn2.finished[2][0] == "越界"
    assert conn2.finished[2][1] is False

    # 已领单据 #1 的结论不随改档翻案：仍是合格 + 警戒。
    assert conn1.finished[1][0] == "合格"
    assert conn1.finished[1][1] is True

    # 新边界内的 210 仍警戒，204 普通合格。
    conn3 = FakeConn({"id": 3, "microstrain": 210.0}, band(205.0, 215.0))
    c3 = worker.claim_one(conn3)
    worker.finish(conn3, c3)
    assert conn3.finished[3][:2] == ("合格", True)

    conn4 = FakeConn({"id": 4, "microstrain": 204.0}, band(205.0, 215.0))
    c4 = worker.claim_one(conn4)
    worker.finish(conn4, c4)
    assert conn4.finished[4][:2] == ("合格", False)

    print("test_worker_snapshot OK")


if __name__ == "__main__":
    main()
