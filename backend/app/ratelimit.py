"""slowapi 限速器单例。

放在独立模块, 避免 main.py 与 routers/auth.py 互相导入。
key_func 用 get_remote_address —— uvicorn 由 --proxy-headers + --forwarded-allow-ips='*'
信任 Caddy 转发的 X-Forwarded-For, request.client.host 就是真实客户端 IP。

存储后端: 默认 memory:// (单 worker 进程一致)。横向扩展 (多 uvicorn worker
或多 backend 容器) 时需切 Redis: Limiter(..., storage_uri="redis://redis:6379")。
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
