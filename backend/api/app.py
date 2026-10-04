import os
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext
from sanic import Sanic
from sanic.response import json as sanic_json

from db import (
    create_pool,
    ensure_schema,
    get_active_band,
    insert_band_version,
    list_band_versions,
    seed_if_empty,
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


def serialize_band(band: dict | None) -> dict | None:
    if not band:
        return None
    return {
        "id": band["id"],
        "warn_inner": band["warn_inner"],
        "warn_outer": band["warn_outer"],
        "changed_by": band["changed_by"],
        "changed_at": _iso(band["changed_at"]),
        "note": band.get("note"),
    }


READING_COLUMNS = """
    SELECT r.id, r.span_code, r.microstrain, r.verdict, r.reason, r.status,
           r.created_by, r.created_at, r.processed_at,
           r.warning, r.warn_inner, r.warn_outer, r.band_version_id,
           b.warn_inner AS band_inner, b.warn_outer AS band_outer,
           b.changed_by AS band_changed_by, b.changed_at AS band_changed_at
    FROM strain_readings r
    LEFT JOIN warning_band_versions b ON b.id = r.band_version_id
"""


def serialize_reading(r: dict) -> dict:
    """单据对外结构。

    着色与详情脚注共用的唯一边界来源就是单据上领单瞬间抄下的
    warn_inner/warn_outer（及 band_version_id）；前端不得另查当前黄带。
    """
    return {
        "id": r["id"],
        "span_code": r["span_code"],
        "microstrain": r["microstrain"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "status": r["status"],
        "created_by": r["created_by"],
        "created_at": _iso(r["created_at"]),
        "processed_at": _iso(r["processed_at"]),
        "warning": bool(r["warning"]),
        "warn_inner": r["warn_inner"],
        "warn_outer": r["warn_outer"],
        "band_version_id": r["band_version_id"],
        "band": (
            {
                "version_id": r["band_version_id"],
                "warn_inner": r["band_inner"],
                "warn_outer": r["band_outer"],
                "changed_by": r["band_changed_by"],
                "changed_at": _iso(r["band_changed_at"]),
            }
            if r["band_version_id"] is not None
            else None
        ),
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
            await cur.execute(READING_COLUMNS + " ORDER BY r.id DESC")
            rows = await cur.fetchall()
    return sanic_json([serialize_reading(r) for r in rows])


@app.get("/api/readings/<reading_id:int>")
async def get_reading(request, reading_id: int):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(READING_COLUMNS + " WHERE r.id = %s", (reading_id,))
            r = await cur.fetchone()
    if not r:
        return sanic_json({"detail": "单据不存在"}, status=404)
    return sanic_json(serialize_reading(r))


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
                RETURNING id, span_code, microstrain, verdict, reason, status,
                          created_by, created_at, processed_at,
                          warning, warn_inner, warn_outer, band_version_id
                """,
                (span_code, microstrain, user["username"]),
            )
            row = await cur.fetchone()
        await conn.commit()

    return sanic_json(
        {
            "id": row["id"],
            "span_code": row["span_code"],
            "microstrain": row["microstrain"],
            "verdict": row["verdict"],
            "reason": row["reason"],
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": _iso(row["created_at"]),
            "processed_at": None,
            "warning": False,
            "warn_inner": None,
            "warn_outer": None,
            "band_version_id": None,
            "band": None,
            "message": "已入队，后台工人将认领并按领单时黄带判定",
        },
        status=201,
    )


# ---------------------------------------------------------------------------
# 警戒黄带
# ---------------------------------------------------------------------------


@app.get("/api/warning-band")
async def read_warning_band(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    band = await get_active_band(pool)
    return sanic_json({"band": serialize_band(band)})


@app.get("/api/warning-band/versions")
async def read_warning_band_versions(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    bands = await list_band_versions(pool)
    return sanic_json([serialize_band(b) for b in bands])


@app.put("/api/warning-band")
async def update_warning_band(request):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    # 复核侧只看不能改黄带
    if user["role"] != "writer":
        return sanic_json({"detail": "仅测量员可修改警戒黄带，复核侧只读"}, status=403)

    body = request.json or {}
    try:
        warn_inner = float(body.get("warn_inner"))
        warn_outer = float(body.get("warn_outer"))
    except (TypeError, ValueError):
        return sanic_json({"detail": "黄带内外边界必须是数字"}, status=400)
    if not (warn_inner < warn_outer):
        return sanic_json({"detail": "黄带内边界必须小于外边界"}, status=400)

    note = body.get("note")
    if note is not None:
        note = str(note).strip() or None

    pool = request.app.ctx.pool
    band = await insert_band_version(
        pool, warn_inner, warn_outer, user["username"], note
    )
    return sanic_json({"band": serialize_band(band)})
