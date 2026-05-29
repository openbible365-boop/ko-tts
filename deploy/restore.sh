#!/usr/bin/env bash
set -euo pipefail

# Restore 演练: 从 R2 拿一份备份, 还原到临时 DB, 校验表清单, 然后 drop。
# 不动生产 DB。
# 用法:
#   ./restore.sh                # 拿最近一份
#   ./restore.sh backups/xxx.pgdump   # 指定 key

cd "$(dirname "$0")"
COMPOSE="docker compose -f docker-compose.prod.yml"

KEY="${1:-}"
TMPDB="kotts_restore_test_$(date +%s)"

step() { echo; echo ">>> $*"; }

# 任何位置失败都尝试 drop 临时 DB, 避免污染
cleanup() {
  rc=$?
  echo
  $COMPOSE exec -T postgres sh -c \
    'dropdb --if-exists -U "$POSTGRES_USER" "'"$TMPDB"'"' >/dev/null 2>&1 || true
  $COMPOSE exec -T postgres rm -f /tmp/restore.pgdump >/dev/null 2>&1 || true
  if [ $rc -ne 0 ]; then echo ">>> 演练 FAILED (exit $rc)"; fi
}
trap cleanup EXIT

if [ -z "$KEY" ]; then
  step "[0] 选取最新备份"
  KEY=$($COMPOSE exec -T backend python -c '
import asyncio, sys
from app import storage
from app.config import settings
async def main():
    async with storage._client() as s3:
        resp = await s3.list_objects_v2(Bucket=settings.r2_bucket, Prefix="backups/")
        items = sorted(resp.get("Contents", []), key=lambda o: o["LastModified"], reverse=True)
        if not items:
            sys.exit("no backups under backups/ prefix")
        print(items[0]["Key"])
asyncio.run(main())
' | tr -d '\r')
fi
echo "    key: $KEY"

step "[1] 从 R2 下载并写入 postgres 容器 /tmp/restore.pgdump"
$COMPOSE exec -T backend python -c "
import asyncio, sys
from app import storage
async def main():
    data = await storage.get_bytes('$KEY')
    sys.stdout.buffer.write(data)
asyncio.run(main())
" | $COMPOSE exec -T postgres sh -c 'cat > /tmp/restore.pgdump'
$COMPOSE exec -T postgres sh -c 'ls -lh /tmp/restore.pgdump'

step "[2] 建临时 DB $TMPDB"
$COMPOSE exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" "'"$TMPDB"'"'

step "[3] pg_restore -> $TMPDB"
$COMPOSE exec -T postgres sh -c \
  'pg_restore --no-owner --no-acl -U "$POSTGRES_USER" -d "'"$TMPDB"'" /tmp/restore.pgdump'

step "[4] 比对表清单 (主库 vs 还原库)"
MAIN=$($COMPOSE exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT table_name FROM information_schema.tables WHERE table_schema='\''public'\'' ORDER BY 1;"' | tr -d '\r')
REST=$($COMPOSE exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "'"$TMPDB"'" -tAc "SELECT table_name FROM information_schema.tables WHERE table_schema='\''public'\'' ORDER BY 1;"' | tr -d '\r')

if [ "$MAIN" = "$REST" ]; then
  echo "    ✓ 表清单一致:"
  echo "$REST" | sed 's/^/      - /'
else
  echo "    ✗ 表清单不一致:"
  diff <(echo "$MAIN") <(echo "$REST") || true
  exit 1
fi

step "[5] 行数抽查 (recordings/segments/users)"
$COMPOSE exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "'"$TMPDB"'" -c "SELECT '\''users'\'' AS t,count(*) FROM users UNION ALL SELECT '\''recordings'\'',count(*) FROM recordings UNION ALL SELECT '\''segments'\'',count(*) FROM segments;"'

echo
echo ">>> ✅ restore 演练通过 (临时 DB 已 drop)"
