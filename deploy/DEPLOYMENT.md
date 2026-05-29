# 部署流程 — ko-tts

生产部署到 `kr-tts.openbible.live`(VPS `149.28.149.67`,以 `deploy` 用户)。
镜像/编排:`docker compose -f docker-compose.prod.yml`(postgres + backend + caddy)。

---

## 架构

```
公网 :443/:80  ──►  caddy (自动 HTTPS / ACME)
                      └─► backend:8000 (FastAPI, uvicorn)
                            └─► postgres:5432 (内部网络, 不对外)
```

- 三个服务都在内部网络 `ko-tts-net`,只有 Caddy 映射 80/443 到宿主机。
- PostgreSQL 数据持久化在宿主机 `/opt/ko-tts/data/postgres`。
- Caddy 的证书/配置在 named volume `caddy_data` / `caddy_config`(docker 管理)。

---

## 前置条件

1. **服务器已初始化**:`deploy/setup-server.sh` 已跑完(Docker、deploy 用户、ufw 放行 80/443、防火墙等)。见 [SERVER_SETUP.md](SERVER_SETUP.md)。
2. **DNS 已指向**:`kr-tts.openbible.live → 149.28.149.67`(已验证)。Caddy 签发证书依赖这条。
3. **本地能用 key 以 deploy 登录**:`ssh deploy@149.28.149.67`。
4. ⚠️ **后端代码已存在**:`backend/` 下需有
   - `pyproject.toml` + `uv.lock`(Dockerfile 用 `uv sync --frozen`,**没有 uv.lock 会构建失败**)
   - `app/main.py`,且暴露 `GET /health`(返回 200;Caddy 探活 + deploy.sh 第 8 步都依赖它)
   - `alembic/` + `alembic.ini`(第 7 步 `alembic upgrade head` 依赖)
   > 目前 `backend/` 尚未创建,deploy.sh 会在构建阶段失败。这是下一步的工作。
5. **填好生产环境变量**:
   ```bash
   cp deploy/.env.prod.example deploy/.env.prod
   # 编辑 deploy/.env.prod, 填 POSTGRES_PASSWORD / R2_* / JWT_SECRET 等
   # JWT_SECRET 可用: openssl rand -hex 32
   ```
   `deploy/.env.prod` 和 `deploy/.env` 已在 `.gitignore`,不会进版本库。

---

## 一键部署

```bash
./deploy/deploy.sh                 # 默认 deploy@149.28.149.67
./deploy/deploy.sh deploy@1.2.3.4  # 指定其他主机
```

脚本(`set -euo pipefail`,逐步打印进度)做 8 件事:

| 步骤 | 动作 |
|---|---|
| 1 | 检查本地 `deploy/.env.prod` 存在,否则报错退出 |
| 2 | `rsync -az --delete` 同步项目到 `/opt/ko-tts/`(排除 `.git/.venv/__pycache__/node_modules/*.pyc/.env`,并**保护** `/data/`) |
| 3 | 远端 `cp deploy/.env.prod → deploy/.env`(chmod 600) |
| 4 | `docker compose pull`(postgres / caddy 镜像) |
| 5 | `docker compose build backend` |
| 6 | `docker compose up -d` |
| 7 | 等 10s,容器内 `alembic upgrade head` |
| 8 | 本地 `curl https://kr-tts.openbible.live/health`,失败则退出码非零 |

> **数据安全**:第 2 步用了 `--delete`(镜像同步,会删远端多余文件),但显式 `--exclude '/data/'`
> 保护数据库目录,被排除的文件不会被 `--delete` 删除。`deploy/.env` 同理(被 `.env` 规则排除)受保护。

---

## 手动等价命令(排查/分步时用)

```bash
# 在 VPS 上
cd /opt/ko-tts/deploy
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f --tail=100         # 全部
docker compose -f docker-compose.prod.yml logs -f caddy              # 看 ACME 证书签发
docker compose -f docker-compose.prod.yml exec -T backend alembic upgrade head
docker compose -f docker-compose.prod.yml exec backend bash          # 进容器
```

---

## 验证

```bash
# 本地
curl -i https://kr-tts.openbible.live/health         # 期望 200
curl -I https://kr-tts.openbible.live/               # 看安全头 / TLS

# VPS 上
docker compose -f docker-compose.prod.yml ps         # 三个服务 Up / healthy
docker compose -f docker-compose.prod.yml exec -T postgres pg_isready
```

首次签发证书可能要几十秒;若 `/health` 一开始 503/502,先看 `caddy` 和 `backend` 日志。

---

## 更新发布

改完代码(后端或 deploy 配置)后,重复 `./deploy/deploy.sh` 即可:rsync 增量同步 → 重建 backend → `up -d` 滚动重启 → 迁移 → 健康检查。Postgres 数据保留。

---

## 常见问题

- **证书签不下来**:确认 DNS 仍指向本机、ufw 放行 80/443、`caddy` 日志里的 ACME 错误。80 端口必须可达(HTTP-01 挑战)。
- **backend 起不来**:`docker compose logs backend`。常见是 `.env` 里 `DATABASE_URL` 写错(host 必须是 `postgres`)或缺 R2 凭据。
- **alembic 报连不上库**:postgres 健康检查未过就跑了迁移;`docker compose ps` 看 postgres 是否 healthy,必要时重跑 `deploy.sh`。
- **构建失败 `uv.lock not found`**:`backend/` 缺 `uv.lock`,在 backend 目录 `uv lock` 生成并提交。
- **想回滚**:`git`(初始化后)切到旧 commit 重新 `deploy.sh`;数据库迁移回滚用 `alembic downgrade`。
- **改了 `POSTGRES_PASSWORD` 但 backend 仍报 `password authentication failed`**:Postgres 只在**首次初始化**数据目录(`/opt/ko-tts/data/postgres`)时按 `POSTGRES_PASSWORD` 建角色;目录已存在时改密码不生效。
  - 库里无重要数据 → 停服后 `sudo rm -rf /opt/ko-tts/data/postgres` 再 `up -d` 重新初始化;
  - 想保留数据 → 进容器同步角色密码(非破坏性):
    ```bash
    cd /opt/ko-tts/deploy
    PW=$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)
    printf "ALTER USER kotts WITH PASSWORD '%s';" "$PW" \
      | docker compose -f docker-compose.prod.yml exec -T postgres psql -U kotts -d kotts
    ```

---

## 稳定后再做

- Caddyfile 取消注释启用 **HSTS**。
- 给后端镜像加 `.dockerignore`(忽略 `.venv __pycache__ .git` 等),加快构建。
- 数据库定期备份(`pg_dump` → R2)。
