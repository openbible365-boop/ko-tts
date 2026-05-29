# ko-tts — 朝鲜语 TTS 数据采集服务

> 共享上下文文档。后续对话以此为准。
> 最后更新: 2026-05-26

## 1. 项目目标

收集**朝鲜语**(한국어 / Korean)基督教内容音频,用于训练 TTS 模型。

- 内容类型: 讲道 / 圣经朗读 / 赞美诗
- 架构: 公网部署**采集服务**(本仓库) + 本地**训练模型**(另行处理)
- 本仓库范围: 仅采集 web 服务 + 其部署,不含训练代码

## 2. 技术栈 — 已确定,不要改 🔒

以下决策已锁定。除非我明确要求,**不要**替换、升级大版本或引入替代方案:

| 层 | 选型 |
|---|---|
| 后端语言 | Python 3.12 |
| Web 框架 | FastAPI |
| ORM | SQLAlchemy 2.x (async) |
| 迁移 | Alembic |
| DB 驱动 | asyncpg |
| 数据库 | PostgreSQL 16 (Docker) |
| 反向代理 | Caddy (自动 HTTPS) |
| 部署 | Docker Compose |
| 包管理 | uv |
| 对象存储 | Cloudflare R2,bucket `ob-tts-data` |

## 3. 基础设施

### VPS
- 提供商: Vultr,Singapore,vhf 系列
- 系统: Ubuntu 24.04 x64
- IP: `149.28.149.67`
- 登录: `root`(SSH key)
- 域名: `kr-tts.openbible.live`(DNS 已 A 记录指向 VPS IP — 已验证解析正确)

### 本地
- macOS,项目目录: `/Users/joshua/Desktop/Projects/ko-tts`
- git 仓库: <https://github.com/openbible365-boop/ko-tts>(private,SSH 推送,key 已加到 GitHub)

### 对象存储
- Cloudflare R2,bucket: `ob-tts-data`
- 凭据由我手填入 `.env`,Claude 无需知道具体值

## 4. 工作方式约定 🔒

- **一步步来**,不要跳步。未经我确认不要提前写部署脚本 / 大规模脚手架。
- 凭据(R2、DB 密码等)我自己填 `.env`,不要写入仓库,要进 `.gitignore`。

## 5. 进度

- [x] DNS 解析验证(`kr-tts.openbible.live` → 149.28.149.67)
- [x] 创建 SPEC.md
- [x] 本地 SSH 接入 VPS(key 登录已验证 ✅,2026-05-26)
- [x] 服务器初始化(`deploy/setup-server.sh` 已在 VPS 执行成功;手册 `deploy/SERVER_SETUP.md`)
- [x] 部署文件已写:`deploy/Dockerfile`、`docker-compose.prod.yml`、`Caddyfile`、`.env.prod.example`、`deploy.sh`、`DEPLOYMENT.md`;根目录 `.gitignore`
- [x] 后端骨架 `backend/`(app/main.py 含 `/health`、config、async db、alembic 异步 env.py、.dockerignore、**uv.lock 已生成**,38 包,py3.12 sync + import + alembic 离线均验证通过)
- [x] **首次部署成功(2026-05-26),整条链路验证通过**:`https://kr-tts.openbible.live/health` → HTTP/2 200 `{"status":"ok"}`;Caddy 已签发 Let's Encrypt 正式证书;postgres/backend 容器 healthy;alembic 能连库
- [x] **数据模型 + 首个迁移 `0001` 已上线**(users / recordings / segments;UUID 主键、timestamptz、status 存 String、命名约定固定;`alembic check` 通过,DB 与模型零漂移)
- [x] **R2 客户端封装 `app/storage.py`(aioboto3)**:put/get/delete/head/exists、预签名 PUT/GET、键构造、check_connection;已对真实 `ob-tts-data` 做往返测试通过(从 VPS 容器内)。R2_* 已填入 `deploy/.env.prod`
- [x] **鉴权(JWT)已上线**:开放自助注册(默认 contributor)、登录(OAuth2 password flow)、`/auth/me`;bcrypt 密码哈希、PyJWT HS256、access-only token(7 天)、`get_current_user`/`require_role` 依赖。线上 register/login/me + 401/409/422 全验证通过
- [x] **上传 recording 接口已上线**(预签名直传):`POST /recordings`(建行 `pending_upload` + 返回预签名 PUT URL)、`POST /recordings/{id}/complete`(head_object 校验 + 转 `uploaded` + 记 file_size)、`GET /recordings`(本人;reviewer/admin 看全部)、`GET /recordings/{id}`、`GET /recordings/{id}/download-url`(预签名 GET)。线上端到端(含本地直传 R2 + 下载校验)全通过
- [x] **自动切分(worker)已上线**:新增 `worker` 容器(Postgres 轮询 `FOR UPDATE SKIP LOCKED` 领取 `uploaded` 录音)→ ffmpeg `silencedetect` 按静音切 → 每段切单声道 wav(24kHz)传 R2 → 建 segment 行(`pending_transcription`);录音 `uploaded→segmenting→segmented` 并回填 duration/sr/channels/codec。API:`POST /recordings/{id}/segment`(重切)、`GET /recordings/{id}/segments`。线上合成音频测试:3 段精确切分、切片入 R2、元数据正确
- [x] **ASR 已上线(本地 faster-whisper)**:worker 同时处理切分和 ASR(优先 ASR);medium + int8 量化,模型缓存在 `worker_models` named volume(`HF_HOME`/`XDG_CACHE_HOME` 重定向到 `/models` 避免 xet 写入 $HOME 失败);segment 状态机加 `transcribing` / `transcription_failed`(String 列,无需迁移)。线上真韩语(macOS Yuna)验证:`uploaded→segmenting→segmented→transcribing→pending_correction`,asr_text 接近完美(仅 `시염`/`시험` 一字之差)
- [x] **人工校对/审核 API 已上线**:新 `app/routers/segments.py` 提供 `GET /segments`(支持 status/recording_id 过滤;contributor 仅见自己上传的)、`GET /segments/{id}`、`GET /segments/{id}/download-url`(预签名 GET 切片)、`POST .../correct`(reviewer/admin,`pending_correction|rejected→pending_review`,写 text/corrected_by/at)、`POST .../approve`(`pending_review→approved`,清空 rejection_reason)、`POST .../reject`(`pending_review→rejected`,记 reason)。线上验证:状态机/权限/409/403/list/下载全通过

### ✅ 数据采集主流程完整闭环(2026-05-29)
```
投稿者上传 → uploaded → [worker] segmenting → segmented
  └── 每段: pending_transcription → [worker] transcribing → pending_correction (asr_text)
         └── reviewer correct → pending_review (text)
                ├── approve → approved  ← 进训练集
                └── reject  → rejected ←→ (回到 correct)
```

- [x] **管理端点 `/admin/users` 已上线**:`GET`(分页 + role / is_active / email_like 过滤)、`GET /{id}`、`PATCH /{id}`(role/is_active 至少一项;**self-demote/self-deactivate 守护 400 防 admin 锁死**);停用用户的 token 立即失效(`get_current_user` 已查 `is_active`)。**首个 admin 仍靠 psql 引导**(`UPDATE users SET role='admin' WHERE email='...';`),之后均走 API

- [x] **DB 备份已上线**(2026-05-29):`deploy/backup.sh`(pg_dump -Fc 通过宿主机 shell 管道串 backend 容器流式上传 R2,无中间落盘)+ `app/backup.py`(`upload`/`prune` CLI)+ `deploy/restore.sh`(下载 → 临时 DB → 校验表清单 → drop)。**daily 03:00 UTC** 由 deploy 用户 crontab 调度,保留 30 天,日志 `/opt/ko-tts/logs/backup.log`。首次备份 + restore 演练已通过

- [x] **自动化测试 + CI 已上线**(2026-05-29):`tests/unit/`(纯函数:`compute_segments` / `security` / Pydantic schemas)+ `tests/integration/`(httpx ASGITransport,真 Postgres,truncate-between-tests)。conftest 在 import app 前设 env 默认值;`need_db` fixture 让本地无 `KOTTS_TEST_DATABASE_URL` 时集成测试自动 skip。`.github/workflows/ci.yml`:ubuntu + `postgres:16-alpine` service + uv + ruff check + pytest。本地 26 unit 通过 / 10 integration skip
- [x] **auth 限速已上线**(slowapi,内存后端):`POST /auth/login` 5/分钟、`POST /auth/register` 3/小时(per-IP,Caddy 转发的真实客户端 IP)。超限返回 429 `{"error":"Rate limit exceeded: ..."}`。线上端到端验证(连发 7 次 login → 第 6 起 429;连发 4 次 register → 第 4 起 429)。横向扩展时切 Redis 后端
- [x] **HSTS 已开**(2026-05-29):Caddyfile `Strict-Transport-Security "max-age=31536000; includeSubDomains"`(不发 preload 字面量,等想提交 hstspreload.org 再加)。deploy.sh 改用 `restart caddy` 替代 `caddy reload`——因为 Caddyfile 是单文件 bind mount,rsync 原子替换留下旧 inode,reload 会看到 "config is unchanged",必须 restart 重建挂载
- [x] **worker reaper 已上线**(2026-05-29):`_reap_stale(timeout_min)`,UPDATE 把 `segmenting>=N分钟` 退回 `uploaded`、`transcribing>=N分钟` 退回 `pending_transcription`。**worker 启动时 aggressive sweep(timeout=0,单 worker 假设)+ 主循环每 5 分钟跑一次(timeout=30 分钟,可配)**。线上注入卡死行→重启 worker→reaper 解锁→主循环重新处理(假数据走 failed 终态),全链路验证

- [x] **切分改"句末停顿就近下刀"**(2026-05-30):原 `compute_segments` 把长语音按 `max_len` 死板硬切(每段恰好 15.0s、常切在句中,不利校对)。改成**目标长度合并 + 静音边界断开**:把语音块合并到 ~`seg_target_segment_sec`(13s),在最近的静音(句末停顿)处收口;只有整段连续无停顿且超 `max`(18s)才兜底硬切。**关键是静音阈值**:对真实朗读(约翰福音1)做参数扫描发现其停顿带室内底噪、从不低于 -30dB,故旧 `-30dB/0.5s` 几乎扫不到停顿;改 **`-21dB/0.25s`** 后稳定捕捉。线上重切验证:`silences=275 -> 48 段`,时长 11~17.3s、中位 14.1s、**0 段硬切**,刀刀落在停顿。参数都在 settings,换录音源/麦克风环境可能要重调。单测 8 例覆盖新语义。

### 前端 UI 启动 — 投稿者上传流 slice(2026-05-29)
- [x] **前端脚手架 `frontend/`(与 backend/ 平级,monorepo)**:**React + Vite + TypeScript SPA**,路由 `react-router-dom`,服务器态 `@tanstack/react-query`,纯 SPA 无 SSR。栈选型 + 首个 slice(投稿者上传流)由 joshua 拍板。
- [x] **auth + 「我的录音」列表已跑通**:登录/注册页(注意 login 是 OAuth2 **form-encoded**)、会话保持(token 存 localStorage `kotts_token`,启动用 `/auth/me` 验活)、路由守卫、录音列表(react-query,流转中状态每 5s 轮询)。`lib/api.ts` fetch 封装 + `lib/endpoints.ts` 类型化调用对齐后端 schema。
- [x] **dev 连后端 = Vite proxy**:`/api/*` 转发到生产 `https://kr-tts.openbible.live` 并剥 `/api` 前缀(后端路由无前缀),浏览器同源不触发 CORS;可用 `VITE_API_PROXY_TARGET` 覆盖。验证:build + lint + proxy 链路(health/401/form 登录)+ 浏览器渲染(登录页 + 守卫重定向)全通过,无 console 报错。
- [x] **上传页已跑通**(2026-05-29):`routes/Upload.tsx` 完整三步流——建行 `POST /recordings` → 浏览器 XHR PUT 直传 R2(带进度条)→ `complete`;`lib/endpoints.ts` 加 `uploadFileToR2()`(XHR 拿进度,PUT 绝对预签名 URL 不经 `/api` 代理)。验证:tsc+lint 全绿、浏览器渲染正常、`POST /recordings` 返 201+预签名 URL;**直传那步在未配 CORS 时如期被 `Failed to fetch` 拦**(印证前置)。
- [x] **R2 CORS 已配 + 端到端验证通过**(2026-05-29):规则 `deploy/r2-cors.json`(仅放行 `http://localhost:5173`,方法 PUT/GET/HEAD),joshua 已贴进 Cloudflare R2 → bucket → Settings → CORS Policy。浏览器实测:建行 201 → 直传 R2 200(进度 100% + ETag)→ complete 200(`file_size` 与对象字节一致)→ 列表显示;worker 自动接手把这条 `uploaded→segmented`,接入既有切分链路。**投稿者上传流 slice 完整闭环。**
- [x] **校对/审核工作台 `routes/Review.tsx` 已写**(2026-05-29):单页 `/review`,四 tab(待校对/待审核/已通过/已退回)按 `status` 拉 `GET /segments`;每段一张卡片:**懒加载预签名音频**(`▶ 加载音频`→`GET /segments/{id}/download-url`→`<audio>`)+ ASR 原文 + 校对文本框(`correct`)/ 通过·退回(`approve`/`reject`,退回带必填理由);rejected 段可重新校对。**reviewer/admin 专属**:topbar 入口按角色显示 + `RequireStaff` 路由守卫(contributor 撞 `/review` 退回首页)。`lib/endpoints.ts` + `types.ts` 加 segment 调用/类型。验证:tsc+lint 全绿、**contributor 门禁实测通过(无入口 + /review 重定向)**、页面挂载渲染无 console 报错(临时放开守卫截图空态后已还原)。**staff 侧动作的端到端验证待做**——需先在 VPS psql 把某账号提成 reviewer(冷启动,同 admin 引导),且需真实切出多段的录音(当前 prod 0 segments)。
- 注:node 经 nvm 装 v24(本机原无 brew/node),详见下方「会咬人的隐藏知识」与 memory `project_frontend_dev_setup`。

### 可选下一步(MVP 后)
- HSTS 打开(一行 Caddyfile)
- auth 限速;自动化测试;CI
- 导出 approved segments(给训练侧的清单/manifest;含预签名 audio_url + text + duration)
- 浏览器直传 R2 时 bucket CORS 配置
- 前端 UI;批量操作;监控/统计

### 切分子系统(2026-05-26)
worker 与 backend 共用同一镜像(含 ffmpeg),`command: python -m app.worker`,无 Redis(DB 即队列)。切分参数在 settings(静音阈值/最短静音/最短最长片段/采样率)。`compute_segments` 为纯函数(已单测:基本切分/丢短/切长/尾静音)。worker 禁用了镜像的 HTTP healthcheck。

### 上传架构决定(2026-05-26)
客户端**预签名直传 R2**(文件不经后端)。为此 joshua **移除了 R2 token 的 IP 锁**(原锁定 VPS,会拒绝任意客户端 IP 的预签名上传)。权衡:token 不再受 IP 保护,靠 `.env` 保密 + presigned URL 对象级/短时效兜底。状态流:`pending_upload`(建行发 URL)→`uploaded`(complete 校验入库)。浏览器直传还需给 bucket 配 CORS(待办)。

### 数据模型(已确定的工作流)
投稿者登录→上传长录音(recording,存 R2)→切成多个 segment→每段 ASR 出 `asr_text`→人工校对成 `text`→人工审核→approved 进训练集。单一目标说话人(不建 speaker 表,recording 记 `uploaded_by` 溯源)。segment 状态机:`pending_transcription→pending_correction→pending_review→approved/rejected`。

### 部署踩坑记录
- Postgres 数据目录 `/opt/ko-tts/data/postgres` 一旦初始化,**改 `POSTGRES_PASSWORD` 不会生效**(镜像只在首次初始化时建角色)。首次部署时该目录已被早先一次手动 `docker compose up` 用占位符密码初始化,导致 backend 用新密码连库报 `password authentication failed`。修法(库为空时):`ALTER USER` 同步密码,或清空该目录重新初始化。已采用 `ALTER USER`(非破坏性)。

### 部署设计要点
- Caddy 自动 HTTPS,反代 `backend:8000`;postgres/backend 不对宿主机暴露端口。
- PG 数据持久化在宿主机 `/opt/ko-tts/data/postgres`;`deploy.sh` 的 `rsync --delete` 用 `--exclude '/data/'` 保护它。
- 本地真实环境变量放 `deploy/.env.prod`(被 .gitignore 忽略),部署时同步并落成远端 `deploy/.env`。

### SSH 接入备注
- 本地密钥: `~/.ssh/id_ed25519`(ed25519,**无 passphrase**),公钥已装到 VPS `root` 的 authorized_keys。
- 指纹: `SHA256:zOIbXXNApZoxEL3yTbNtfdSM44tg+7zvB7R6KNcexNA`
- 验证: `ssh -o BatchMode=yes -i ~/.ssh/id_ed25519 root@149.28.149.67` → `key-login-ok`,host `sg-vhf`,Ubuntu 24.04.4 LTS。
- 可选待办: 给 `~/.ssh/config` 加 host 别名;给 key 加 passphrase;后续考虑禁用 root 密码登录。

---

## 🚀 下次接手须知 (last updated 2026-05-29)

> 给打开新会话的我自己,或新 Claude:**这一节 + 上面的"进度"清单 + 自动加载的 memory,通常就够上手。**

### 此刻的快照
- 后端 MVP **完整**,部署在 https://kr-tts.openbible.live(4 容器全 healthy)
- **前端 `frontend/` 投稿者上传流 slice 完成 + 已 commit(`dc43808`)**:React+Vite SPA,auth + 「我的录音」列表 + **上传页**(建行→直传 R2→complete)全跑通。R2 CORS 已配(`deploy/r2-cors.json`,localhost:5173),浏览器端到端验证通过(`uploaded→segmented`)。
- 测试 90 个,CI 全绿(`https://github.com/openbible365-boop/ko-tts/actions`)
- 最新 commit `302fecd`,11 commits total,branch=main(前端改动 + `.claude/settings.local.json` 的 env/白名单改动均未提交)
- prod 库内容:**1 个 contributor**(jmdsong@gmail.com),0 recordings/0 segments
- **前端 dev 怎么跑**:node 经 nvm 装在 `~/.nvm`(本机无 brew),已把 node bin 前置进 `.claude/settings.local.json` 的 `env.PATH`,所以**重启会话后**裸 `npm`/`node` 即可用,不必再 source nvm。dev server 用 Vite proxy 连生产 API。细节见 memory `project_frontend_dev_setup`。
- 想测试 admin API 前要先 psql 提权一次(冷启动 only):
  ```sql
  UPDATE users SET role='admin' WHERE email='jmdsong@gmail.com';
  ```

### 推荐阅读顺序(冷启动)
1. 本节 + 上面的"进度"列表 —— 边界 + 现状
2. 自动加载的 memory(`~/.claude/projects/-Users-joshua-Desktop-Projects-ko-tts/memory/`):**协作风格** + **SPEC 引用** + **R2 token IP 状态**
3. `git log --oneline -15` —— 11 commits 全是 conventional 格式,扫一遍知本周做了啥
4. 代码入口(后端):`backend/app/main.py` → 5 个 router 文件 → `worker.py`
4b. 代码入口(前端):`frontend/src/App.tsx`(路由)→ `lib/api.ts` + `lib/endpoints.ts`(对接后端)→ `auth/` + `routes/`;`vite.config.ts`(proxy)
5. 运维入口:`deploy/deploy.sh` + `DEPLOYMENT.md` + `backup.sh`

### 自然的下一步(每条都自带没拍板的设计点)

| 方向 | 量 | 还没拍板的事 |
|---|---|---|
| **前端 UI** | 进行中 | 栈已定(React+Vite SPA,无 SSR,无 admin 模板)。auth+录音列表+上传页已跑通;**下一块**待拍板(切片播放/校对页?部署前端?) |
| **训练侧 fetch 脚本** | 1h | 输出格式(LJSpeech-like 还是 JSONL+meta);本地缓存策略 |
| **镜像瘦身** | 2-3h | 拆 backend / worker 两个 Dockerfile;backend 能从 1.58GB → ~300MB |
| **CORS for 浏览器直传** | 1h | 给 `ob-tts-data` 配 CORS;**已成为上传页的硬前置**——浏览器 presigned PUT 没它会被拦 |
| **HSTS preload 提交** | 30min + 数周等审核 | **不可逆**,提交前确定 `kr-tts.openbible.live` 永远 HTTPS |
| **密码重置 / 邮箱验证** | 各 1-2h | 要不要 SMTP;短期 token vs 邮件链接 |
| **补完集成测试** | 已 60+,可继续加 | 目前覆盖 auth/recordings/segments/admin/export 五大 router |

### 会咬人的隐藏知识(都已修,但要知道为啥那样写)

1. **Docker 单文件 bind mount,改文件后容器看不到新内容** → 要 restart 容器(不是 reload)。`deploy.sh` 已自动 restart caddy。改 `compose.yml` 里别再回头加文件级 bind mount。
2. **HuggingFace xet 下载器除 `HF_HUB_CACHE` 还按 `$HOME/.cache` 写** → 容器里 `$HOME=/app` 是只读源码区,Permission denied。修法在 `worker` 服务的 env:`HF_HOME=/models` + `XDG_CACHE_HOME=/models/.cache`。
3. **Pytest SQLAlchemy 引擎跨 loop 失效** → `pyproject.toml` 设 `asyncio_default_{fixture,test}_loop_scope = "session"`。改成 function 立马 "Event loop is closed"。
4. **`rsync --delete` 会扫宿主机数据卷** → `deploy.sh` 显式 `--exclude '/data/'` 和 `--exclude '/logs/'`;**改这两条 = 丢库/丢日志**。
5. **R2 token 现在没有 IP 限制**(为了客户端 presigned PUT)→ token 安全只靠 `.env.prod` 不泄漏。如果以后改成 backend 中转上传,可以加回 IP 锁,详见 memory `project_r2_token_ip_lock`。
6. **`test_recordings.py::_auth` 不能 async**(其它 router 的 `_auth` 也都是 sync)—— 一个一行的 typo 在 CI 里炸了 15 条用例;本地 skip 看不到,所以"只改测试代码"的 PR CI 第一次飘红别紧张,push 再试就行。
7. **前端 node 经 nvm,不在默认 PATH** → 已把 node bin 前置进 `.claude/settings.local.json` 的 `env.PATH`(完整字面值,不依赖 `${PATH}` 展开),**重启会话后**裸 `npm`/`node` 才生效。冷启动若没重启、又见到 `export NVM_DIR=...; . nvm.sh; nvm use; npm ...` 被安全启发式反复拦的确认页,那是误报——重启即解。node 版本变了,这个 `env.PATH` 和 preview `launch.json` 的绝对路径都要同步改。

### 不要随手改的清单
- `RecordingStatus` / `SegmentStatus` **改/删现有值**会破坏现存数据(新增 OK,因为存 String 列)
- `app/db.py` 的命名约定改了 → `alembic check` 会显示一堆伪迁移
- 本地 `deploy/.env.prod` 和 VPS 上 `/opt/ko-tts/deploy/.env` 是同源(deploy.sh 拷的);**别两边各改各的,会漂移**

### 会话开场白(给冷启动的新 Claude 抄)
> "今天继续 ko-tts。先看 SPEC.md 底部'下次接手须知',然后我想做 _\<方向\>_。开始前先和我对齐 1-2 个设计决定。"
