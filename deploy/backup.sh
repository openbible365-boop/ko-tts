#!/usr/bin/env bash
set -euo pipefail

# 每日 DB 备份: pg_dump -Fc -> R2 (经 backend 镜像内 app.backup 模块流式上传)。
# 在 VPS 上以 deploy 用户运行 (cron 调度);也可手动跑做演练。

cd "$(dirname "$0")"          # /opt/ko-tts/deploy
COMPOSE="docker compose -f docker-compose.prod.yml"

LOG_DIR="/opt/ko-tts/logs"    # deploy 用户拥有 /opt/ko-tts, 无需 sudo
LOG="${LOG_DIR}/backup.log"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
mkdir -p "$LOG_DIR"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
KEY="backups/ko-tts-${TS}.pgdump"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"; }

# 任何子命令失败都把上下文写日志后退出 (set -e 会触发 trap)
trap 'rc=$?; log "FAILED (exit $rc)"; exit $rc' ERR

log "=== backup start: key=$KEY retention=${RETENTION_DAYS}d ==="

# postgres 容器里跑 pg_dump (env 已由 compose 注入), 直接管道给 backend 容器上传。
# 两个 exec -T 通过宿主机 shell pipe 串起来, 不会落地中间文件。
$COMPOSE exec -T postgres sh -c \
  'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  | $COMPOSE exec -T backend python -m app.backup upload "$KEY" \
  | tee -a "$LOG"

log "prune backups older than ${RETENTION_DAYS}d"
$COMPOSE exec -T backend python -m app.backup prune "$RETENTION_DAYS" \
  | tee -a "$LOG"

log "=== backup OK ==="
