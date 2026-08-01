# travel-web-api

云途大众版的私有 Web BFF。它位于 `travel-web` 与 `hermes-travel`
之间，负责邀请制邮箱登录、服务端会话、攻略归属、公测生成额度、七天攻略
历史，以及 `travel-admin` 所需的管理员接口和操作审计。

仓库主线：**`main` / `origin/main` = `87bdc65`，仍不包含 v0.1.1 源码**

生产状态：**v0.1.1 Verified Artifact Deployed / Formal User UAT Not Recorded**。
生产运行时已包含 Unique Display Name 与 Alembic `0009`。经验证的恢复源码现位于
未提交分支 `codex/v0.1.1-source-integration`，状态为 **Source Integration Accepted /
Commit Pending / Deployment Pending**；尚未提交或推送到仓库主线。从 `main` 重建
BFF 仍存在覆盖线上 v0.1.1 的风险。
当前门禁见 [v0.1.1 Source Integration Gate](docs/v0.1.1-source-integration-gate.md)。

## Boundary

```text
Browser / travel-web
  -> travel-web-api
       -> authentication and session
       -> quota reservation and settlement
       -> trip ownership and history
       -> internal HTTP call
            -> hermes-travel

Browser / travel-admin
  -> travel-web-api /api/admin/*
       -> administrator authorization and audit
       -> PostgreSQL
```

`travel-web-api` 不负责城市数据、路线规划、Writer、Review、采集或
POI 数据治理；这些仍由 `hermes-travel` 负责。

`travel-admin` 是只面向所有者、管理员和未来运营人员的独立静态前端，
只调用经过授权的管理 API，不直连 PostgreSQL，也不直接调用
`hermes-travel`。普通用户的登录、额度、攻略、失败记录、PDF 和账号注销
全部属于 `travel-web`。

首发邮箱验证码通过阿里云 DirectMail API 发送，使用
`no-reply@notify.kakarot8.com`；验证码生命周期和风控仍由本项目负责。

攻略对用户展示七天，随后进入永久内部内容归档。账号注销删除邮箱、身份和
Session，并解除攻略归档与用户的关联；脱敏后的攻略内容和质量数据继续保留。

v0.1.1 只增加全局唯一、可修改的 Display Name：注册时生成默认名称，用户
可按冷却规则修改，旧名保护 15 天。它不参与登录、权限、所有权或账号合并。

v0.2 计划增加 Linux.do OAuth：活跃、未禁言且首次注册时达到 L1 的
Linux.do 用户免邀请码注册。Google、支付、订阅和公开社区均不属于 v0.2。

## Stack

- Python 3.12
- FastAPI + Uvicorn
- PostgreSQL
- SQLAlchemy 2 + asyncpg
- Alembic
- httpx
- Pydantic Settings
- pytest

## Local development

```bash
uv sync --locked
uv run alembic upgrade head
uv run uvicorn src.app:app --host 127.0.0.1 --port 6670 --reload
```

质量门禁：

```bash
uv run ruff check .
uv run ruff format --check .
TEST_DATABASE_URL=postgresql+asyncpg://<local-role>:<local-password>@127.0.0.1:<port>/travel_web_test uv run pytest
```

PostgreSQL 集成测试拒绝非 `travel_web_test*` 数据库，不能用 SQLite
代替并发与事务证据。

## Documents

- [Product scope](docs/product-scope.md)
- [Architecture and security](docs/architecture-and-security.md)
- [API contract](docs/api-contract.md)
- [Database and quota](docs/database-and-quota.md)
- [Implementation and acceptance plan](docs/implementation-plan.md)
- [Implementation checklist](docs/implementation-checklist.md)
- [P0-P3 local acceptance evidence](docs/acceptance/p0-p3-local-evidence.md)
- [J0.5 local joint-integration runbook](docs/local-joint-integration.md)
- [J0.5 BFF preparation evidence](docs/acceptance/j0.5-bff-integration-evidence.md)
- [J1 SSE non-terminal EOF repair evidence](docs/acceptance/j1-sse-non-terminal-eof-repair-evidence.md)
- [v0.1 and v0.2 release roadmap](docs/release-roadmap.md)
- [v0.1.1 source integration gate](docs/v0.1.1-source-integration-gate.md)
