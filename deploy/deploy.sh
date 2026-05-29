#!/usr/bin/env bash
set -euo pipefail

# 从本地把 ko-tts 部署到 VPS。
# 用法: ./deploy/deploy.sh [VPS_HOST]
#   VPS_HOST 默认 deploy@149.28.149.67
# 详见 deploy/DEPLOYMENT.md

VPS_HOST="${1:-deploy@149.28.149.67}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="/opt/ko-tts"
DOMAIN="kr-tts.openbible.live"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_PROD="${SCRIPT_DIR}/.env.prod"

COMPOSE="docker compose -f docker-compose.prod.yml"

step() { echo; echo ">>> $*"; }
remote() { ssh -i "${SSH_KEY}" "${VPS_HOST}" "$@"; }

# a. 检查本地 .env.prod
step "[1/8] 检查本地 ${ENV_PROD}"
if [[ ! -f "${ENV_PROD}" ]]; then
  echo "错误: 缺少 ${ENV_PROD}" >&2
  echo "请先: cp deploy/.env.prod.example deploy/.env.prod 并填值。" >&2
  exit 1
fi
echo "OK"

# b. rsync 同步项目 (注意: --delete 镜像同步, 但保护宿主机 DB 数据卷 /data/)
step "[2/8] rsync -> ${VPS_HOST}:${REMOTE_DIR}/"
rsync -az --delete \
  -e "ssh -i ${SSH_KEY}" \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude 'node_modules' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude '/data/' \
  --exclude '/logs/' \
  "${REPO_ROOT}/" "${VPS_HOST}:${REMOTE_DIR}/"
echo "OK"

# c. 把 .env.prod 落成远端 deploy/.env (compose 读取)
step "[3/8] 部署 .env.prod -> ${REMOTE_DIR}/deploy/.env"
remote "cp '${REMOTE_DIR}/deploy/.env.prod' '${REMOTE_DIR}/deploy/.env' && chmod 600 '${REMOTE_DIR}/deploy/.env'"
echo "OK"

# d. 拉取镜像 (postgres / caddy)
step "[4/8] docker compose pull"
remote "cd '${REMOTE_DIR}/deploy' && ${COMPOSE} pull"

# e. 构建镜像 (backend + worker 共用同一 Dockerfile)
step "[5/8] docker compose build"
remote "cd '${REMOTE_DIR}/deploy' && ${COMPOSE} build"

# f. 启动 / 更新服务
step "[6/8] docker compose up -d"
remote "cd '${REMOTE_DIR}/deploy' && ${COMPOSE} up -d"

# Caddyfile 是单文件 bind mount: rsync 原子替换会留旧 inode 在容器里,
# 单纯 caddy reload 会看到 "config is unchanged"。restart 重建挂载, ~1s 抖动可接受。
remote "cd '${REMOTE_DIR}/deploy' && ${COMPOSE} restart caddy" || true

# g. 数据库迁移 (在 backend 容器里)
step "[7/8] 等待 10s 后 alembic upgrade head"
sleep 10
remote "cd '${REMOTE_DIR}/deploy' && ${COMPOSE} exec -T backend alembic upgrade head"

# h. 健康检查 (经由公网 + Caddy TLS)
step "[8/8] 健康检查 https://${DOMAIN}/health"
sleep 3
if curl -fsS --max-time 15 "https://${DOMAIN}/health" >/dev/null; then
  echo
  echo "✅ 部署成功, /health 正常。"
else
  echo
  echo "❌ /health 检查失败。排查: ssh ${VPS_HOST} 'cd ${REMOTE_DIR}/deploy && ${COMPOSE} logs --tail=100'" >&2
  exit 1
fi
