# 桥梁应变班交台

测量员上报跨段编号与微应变读数，后台工人用 `FOR UPDATE SKIP LOCKED` 认领待处理队列，按 **80～220 με** 判定 **合格** 或 **越界**。

## 警戒黄带

合格带外侧可由测量员设置一道**警戒黄带**（内边界～外边界，如 200～220 με）：

- 读数**落入黄带**仍判**合格、可入队**，但结论标记为黄色**「警戒」**，总览列表与读数详情**同色同字**。
- **越过黄带外沿**才判**越界**。例如黄带 200～220：`210 → 合格·警戒`（不是越界），`230 → 越界`。
- **领单快照**：工人在认领的同一事务内把当时最新的黄带边界（内外沿 + 版本号）抄到单据上，随后判定一律以该快照为准。改档只影响改档后新领走的单据，已被领走的单据继续用领单瞬间的边界。
- 列表着色与详情脚注**共用同一份单据快照**（`warn_inner/warn_outer/band_version_id`），不回查当前黄带，杜绝两套边界不一致。
- **复核侧只读**：可查看黄带与改档流水，不能改边界、不能提交读数。
- 顶栏「警戒带」入口进入专页：含边界设置、按当前边界预览的样例色块、改档流水。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python Sanic + psycopg（异步连接池） |
| 工人 | `worker.py`（psycopg 同步，`FOR UPDATE SKIP LOCKED`） |
| 页面 | Mithril.js + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3198 |
| 接口 | http://localhost:8198 |
| PostgreSQL | localhost:54398（库名 `bridgestrain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| surveyor | surv123456 | 测量员，可提交读数 |
| reviewer | rev123456 | 复核员，只读列表 |

## 启动

```bash
cd projects/19-bridge-strain-shift
docker compose up --build
```

健康检查：`GET http://localhost:8198/api/health` → `{"status":"ok","service":"bridge-strain-shift"}`

## 种子数据

| 跨段 | 微应变 | 结论 |
|------|--------|------|
| 跨中S1 | 150 με | 合格 |
| 支座S2 | 40 με | 越界 |

## 本地开发（可选）

```bash
cd backend && pip install -r requirements.txt
python -m sanic api.app --host=0.0.0.0 --port=8000 --single-process
python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8198**。
