import os
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext
from sanic import Sanic
from sanic.response import json as sanic_json

from db import (
    PASS_HIGH,
    PASS_LOW,
    create_pool,
    ensure_schema,
    get_band_config_async,
    seed_if_empty,
    update_band_config_async,
)

SECRET = os.environ.get("JWT_SECRET", "bridge-strain-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "surveyor": {"role": "writer", "password_hash": pwd.hash("surv123456")},
    "reviewer": {"role": "reader", "password_hash": pwd.hash("rev123456")},
}

app = Sanic("bridge-strain-shift")


def _auth_header(request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def _require_user(request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        return None
    return user


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _reading_out(r) -> dict:
    """读数序列化：结论、警戒标志与领单瞬间的黄带快照一起下发。

    前端总览表与详情页一律依据这里的 verdict/warning 着色与措辞，保证
    “同色同字”；band_* 快照用于详情脚注，口径与判定完全一致。
    """
    return {
        "id": r["id"],
        "span_code": r["span_code"],
        "microstrain": r["microstrain"],
        "verdict": r["verdict"],
        "warning": bool(r["warning"]) if r["warning"] is not None else False,
        "reason": r["reason"],
        "status": r["status"],
        "created_by": r["created_by"],
        "created_at": _iso(r["created_at"]),
        "processed_at": _iso(r["processed_at"]),
        "band_enabled": r["band_enabled"],
        "band_inner": r["band_inner"],
        "band_outer": r["band_outer"],
    }


def _band_out(cfg) -> dict:
    return {
        "enabled": bool(cfg["enabled"]),
        "inner_edge": cfg["inner_edge"],
        "outer_edge": cfg["outer_edge"],
        "pass_low": PASS_LOW,
        "pass_high": PASS_HIGH,
        "updated_by": cfg["updated_by"],
        "updated_at": _iso(cfg["updated_at"]),
    }


@app.before_server_start
async def setup(_app, _loop):
    pool = await create_pool()
    _app.ctx.pool = pool
    await ensure_schema(pool)
    await seed_if_empty(pool)


@app.after_server_stop
async def teardown(_app, _loop):
    pool = _app.ctx.pool
    if pool:
        await pool.close()


@app.get("/api/health")
async def health(_request):
    return sanic_json({"status": "ok", "service": "bridge-strain-shift"})


@app.post("/api/auth/login")
async def login(request):
    body = request.json or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        return sanic_json({"detail": "用户名或密码错误"}, status=401)
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return sanic_json(
        {"access_token": token, "username": username, "role": user["role"]}
    )


@app.get("/api/readings")
async def list_readings(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, span_code, microstrain, verdict, warning, reason, status,
                       created_by, created_at, processed_at,
                       band_enabled, band_inner, band_outer
                FROM strain_readings
                ORDER BY id DESC
                """
            )
            rows = await cur.fetchall()
    return sanic_json([_reading_out(r) for r in rows])


@app.post("/api/readings")
async def create_reading(request):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if user["role"] != "writer":
        return sanic_json({"detail": "仅测量员可提交应变读数"}, status=403)
    body = request.json or {}
    span_code = str(body.get("span_code", "")).strip()
    if not span_code:
        return sanic_json({"detail": "跨段编号不能为空"}, status=400)
    try:
        microstrain = float(body.get("microstrain"))
    except (TypeError, ValueError):
        return sanic_json({"detail": "微应变必须是数字"}, status=400)

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO strain_readings (span_code, microstrain, status, created_by, created_at)
                VALUES (%s, %s, 'pending', %s, now())
                RETURNING id, span_code, microstrain, verdict, warning, reason, status,
                          created_by, created_at, processed_at,
                          band_enabled, band_inner, band_outer
                """,
                (span_code, microstrain, user["username"]),
            )
            row = await cur.fetchone()
        await conn.commit()

    out = _reading_out(row)
    out["message"] = "已入队，后台工人将按当前黄带边界认领并判定"
    return sanic_json(out, status=201)


@app.get("/api/warning-band")
async def get_warning_band(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            cfg = await get_band_config_async(cur)
    return sanic_json(_band_out(cfg))


@app.put("/api/warning-band")
async def put_warning_band(request):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if user["role"] != "writer":
        # 复核侧只能看不能改黄带。
        return sanic_json({"detail": "仅测量员可修改警戒黄带"}, status=403)

    body = request.json or {}
    enabled = bool(body.get("enabled", True))
    try:
        inner_edge = float(body.get("inner_edge"))
        outer_edge = float(body.get("outer_edge"))
    except (TypeError, ValueError):
        return sanic_json({"detail": "黄带内沿、外沿必须是数字"}, status=400)

    if not (PASS_LOW <= inner_edge <= outer_edge <= PASS_HIGH):
        return sanic_json(
            {
                "detail": (
                    f"黄带须落在合格带 {_num(PASS_LOW)}～{_num(PASS_HIGH)} με 内，"
                    "且内沿不大于外沿"
                )
            },
            status=400,
        )

    note = body.get("note")
    note = str(note).strip() if note is not None else None
    note = note or None

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            cfg = await update_band_config_async(
                cur,
                enabled=enabled,
                inner_edge=inner_edge,
                outer_edge=outer_edge,
                changed_by=user["username"],
                note=note,
            )
        await conn.commit()
    return sanic_json(_band_out(cfg))


@app.get("/api/warning-band/history")
async def warning_band_history(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, enabled, inner_edge, outer_edge,
                       changed_by, changed_at, note
                FROM warning_band_history
                ORDER BY id DESC
                LIMIT 100
                """
            )
            rows = await cur.fetchall()
    out = [
        {
            "id": r["id"],
            "enabled": bool(r["enabled"]),
            "inner_edge": float(r["inner_edge"]),
            "outer_edge": float(r["outer_edge"]),
            "changed_by": r["changed_by"],
            "changed_at": _iso(r["changed_at"]),
            "note": r["note"],
        }
        for r in rows
    ]
    return sanic_json(out)


def _num(x: float) -> str:
    f = float(x)
    return str(int(f)) if f.is_integer() else f"{f:g}"
