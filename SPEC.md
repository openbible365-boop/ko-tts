# ko-tts — 朝鲜语 TTS 数据采集服务

> 共享上下文文档。后续对话以此为准。
> 最后更新: 2026-05-26

## 1. 项目目标

收集**多语言**基督教内容音频,用于训练 TTS 模型。最初为朝鲜语(한국어 / Korean)单语,2026-05-31 起转向多语言。

- 语种: 英语(en)/ 普通话(zh)/ 朝鲜语(ko) — 上传时选;切片 ASR 按所选语种转写(详见 `app/languages` 思路与 `app/models.py` 的 `Language`)。Whisper 识别层只认基础语言,故只放这三种。
- 内容类型: 播音 / 演讲 / 朗诵(原讲道 / 圣经朗读 / 赞美诗,2026-05-31 改名;底层值 sermon/bible_reading/hymn 未变)
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

- [x] **可选去背景音乐(人声分离)**(2026-05-30):上传时勾选「消除背景音乐」→ `recordings.remove_music`(迁移 0002)。worker 在切分前对 `remove_music` 的录音做**人声分离**(UVR MDX-Net,经 `audio-separator`),用剥掉音乐的人声去做静音检测+切片(顺带让停顿真静音、切得更准)。**打包**:audio-separator 拖 torch,装在独立 `/opt/sepvenv`(Dockerfile `sepbuilder` 阶段,CPU torch 三件套必须配套否则 `torchvision::nms` 不匹配),worker 以**子进程**调 CLI —— torch 不进常驻 whisper 的进程,跑完即释放内存。`app/separation.py`;模型缓存 `/models/audio-separator`。**慢**(~2.2× 实时,12 分钟录音 ~28 分钟),故 reaper `worker_claim_timeout_min` 30→120。线上端到端验证:90 秒带乐片段→人声干净、6 段 ~14s。镜像 1.59G→3.71G(CPU torch)。

### 前端 UI 启动 — 投稿者上传流 slice(2026-05-29)
- [x] **前端脚手架 `frontend/`(与 backend/ 平级,monorepo)**:**React + Vite + TypeScript SPA**,路由 `react-router-dom`,服务器态 `@tanstack/react-query`,纯 SPA 无 SSR。栈选型 + 首个 slice(投稿者上传流)由 joshua 拍板。
- [x] **auth + 「我的录音」列表已跑通**:登录/注册页(注意 login 是 OAuth2 **form-encoded**)、会话保持(token 存 localStorage `kotts_token`,启动用 `/auth/me` 验活)、路由守卫、录音列表(react-query,流转中状态每 5s 轮询)。`lib/api.ts` fetch 封装 + `lib/endpoints.ts` 类型化调用对齐后端 schema。
- [x] **dev 连后端 = Vite proxy**:`/api/*` 转发到生产 `https://kr-tts.openbible.live` 并剥 `/api` 前缀(后端路由无前缀),浏览器同源不触发 CORS;可用 `VITE_API_PROXY_TARGET` 覆盖。验证:build + lint + proxy 链路(health/401/form 登录)+ 浏览器渲染(登录页 + 守卫重定向)全通过,无 console 报错。
- [x] **上传页已跑通**(2026-05-29):`routes/Upload.tsx` 完整三步流——建行 `POST /recordings` → 浏览器 XHR PUT 直传 R2(带进度条)→ `complete`;`lib/endpoints.ts` 加 `uploadFileToR2()`(XHR 拿进度,PUT 绝对预签名 URL 不经 `/api` 代理)。验证:tsc+lint 全绿、浏览器渲染正常、`POST /recordings` 返 201+预签名 URL;**直传那步在未配 CORS 时如期被 `Failed to fetch` 拦**(印证前置)。
- [x] **R2 CORS 已配 + 端到端验证通过**(2026-05-29):规则 `deploy/r2-cors.json`(仅放行 `http://localhost:5173`,方法 PUT/GET/HEAD),joshua 已贴进 Cloudflare R2 → bucket → Settings → CORS Policy。浏览器实测:建行 201 → 直传 R2 200(进度 100% + ETag)→ complete 200(`file_size` 与对象字节一致)→ 列表显示;worker 自动接手把这条 `uploaded→segmented`,接入既有切分链路。**投稿者上传流 slice 完整闭环。**
- [x] **校对/审核工作台 `routes/Review.tsx` 已写**(2026-05-29):单页 `/review`,四 tab(待校对/待审核/已通过/已退回)按 `status` 拉 `GET /segments`;每段一张卡片:**懒加载预签名音频**(`▶ 加载音频`→`GET /segments/{id}/download-url`→`<audio>`)+ ASR 原文 + 校对文本框(`correct`)/ 通过·退回(`approve`/`reject`,退回带必填理由);rejected 段可重新校对。**reviewer/admin 专属**:topbar 入口按角色显示 + `RequireStaff` 路由守卫(contributor 撞 `/review` 退回首页)。`lib/endpoints.ts` + `types.ts` 加 segment 调用/类型。验证:tsc+lint 全绿、**contributor 门禁实测通过(无入口 + /review 重定向)**、页面挂载渲染无 console 报错(临时放开守卫截图空态后已还原)。**staff 侧动作的端到端验证待做**——需先在 VPS psql 把某账号提成 reviewer(冷启动,同 admin 引导),且需真实切出多段的录音(当前 prod 0 segments)。
- [x] **录音删除 + 单录音校对入口**(2026-05-30):「我的录音」每行加 **校对 / 删除** 两个操作。删除→确认弹窗→`DELETE /recordings/{id}`(owner 或 admin;删 R2 切片+原文件,DB 级联删 segment 行)。校对→`/review?recording=<id>`,校对页读 query 只显示该录音的切片(后端 `GET /segments?recording_id=` 早已支持),顶部提示条 + 「查看全部」。线上验证:校对筛选(41/59)、删除全链路(确认弹窗→真删→列表刷新)、真录音不受影响。
- [x] **处理进度反馈**(2026-05-30):录音列表状态格不再是干瘪的「切分中」。新 `GET /recordings/{id}/progress`(段计数 + `phase_elapsed_sec`= now-updated_at)。列表对处理中的录音轮询(3s):**切分/去音乐阶段**(无细粒度信号)显示动画不确定条 + 阶段(「去背景音乐 + 切分中」)+ 已用时;**ASR 阶段**显示真实 `转写 X/N 段` 确定条。解决去音乐那 ~28 分钟看着像冻住的问题。注:worker 重启(每次部署)会触发启动 reap 把 `segmenting` 退回重处理 —— 分离子进程随重启而死,重切是正确的,但频繁部署会反复打断长分离。
- [x] **校对页:tab 顺序 + 已通过段的退回/删除 + 单段删除**(2026-05-30):tab 改为 待校对·待审核·**已退回·已通过**(已退回前移)。已通过卡片头部「已通过」徽章后加 **退回**(`reject` 放开 `approved→rejected`,理由改可选,回到已退回可再改)+ **删除**(新 `DELETE /segments/{id}`,reviewer/admin,删 R2 clip+行)。**部署技巧**:本次只改 backend API(worker 代码没动),用 `docker compose up -d --no-deps backend` 只重建 backend、**不重启 worker**,避免打断正在跑的去音乐分离。线上验证:退回 approved→rejected、删除段、tab 顺序全过。
- [x] **前端已上线 `https://kr-tts.openbible.live/`**(2026-05-30):本地 `npm run build` → `frontend/dist`,rsync 到 VPS,**Caddy 同时服务 SPA + 反代 API**:`handle_path /api/*`→后端(剥 `/api`,同 dev proxy)、`/health`→后端、其余→`file_server` + `try_files … /index.html`(SPA 回退)。compose 给 caddy 加 `../frontend/dist:/srv:ro` 挂载。部署只重建 caddy(`up -d --no-deps caddy`),不动 backend/worker。验证:`/`=SPA、`/assets/*`=200、`/health`+`/api/health`=后端、`/review`=SPA 回退、`/api/auth/login`=token。**注 1**:`dist` 是 gitignore 的构建产物,前端改动要重新 `npm run build` + rsync(deploy.sh 暂未含此步)。**注 2**:浏览器直传 R2 需 CORS 放行新源 —— `deploy/r2-cors.json` 已加 `https://kr-tts.openbible.live`,**待 joshua 在 Cloudflare 重新应用**,否则线上上传会被 CORS 拦(登录/列表/校对/删除不受影响)。
### 训练侧对接起步 — 说话人标注 + 数据集导出(2026-05-30)
- 目标:用 GPT-SoVITS,拿采集的样本训出自定义朝鲜语**男声/女声**。算力:joshua 倾向 Mac-only(MPS 能训但磕碰,建议训练那一步租云 GPU);声音质量天花板取决于数据(10–60 分钟干净单说话人即够)。
- [x] **录音加 `speaker`(声音/说话人)自由文本标注**(迁移 0003,索引):上传页加输入框、列表加「声音」列、`RecordingRead`/`Create` 带上。让数据能按声音分组出男/女。
- [x] **导出 manifest 支持 speaker**:`GET /export/manifest.jsonl`(早已存在,导 approved 段:预签名 audio_url+text+duration)新增 `speaker` 字段 + `?speaker=` 精确筛选。
- [x] **训练侧桥接脚本 `training/build_gptsovits_dataset.py`**(本仓库唯一训练侧代码,纯标准库):调 manifest → 下载 wav → 写 GPT-SoVITS 标注表 `wav|speaker|ko|text`。线上端到端验证(造 approved 段 + 真 wav → 脚本筛 speaker → 下载 + 生成 train.list,格式/采样率正确)。注:脚本加了 `from __future__ import annotations` 兼容旧 python3(joshua 本机 python3=3.9)。
- [x] **一键导出数据集按钮**(2026-05-30):校对页头部「⬇ 导出数据集(已通过)」→ `GET /export/dataset.zip`(staff;内存组 zip:`wavs/<id>.wav` + GPT-SoVITS `train.list`(`wav|speaker|ko|text`)+ README;支持 `?speaker=`/`?content_category=`/`?status=` 筛选)。前端带鉴权头 fetch→blob 触发下载。线上实测:48 段 approved → 31.4MB zip、48 wav + train.list(已带 speaker `남성1`),wav 校验为合法 RIFF。
- [x] **切片采样率 24k→32k**(2026-05-30):`seg_sample_rate=32000`(GPT-SoVITS 内部即 32k,取之为保真上限)。**只影响之后新切的段**;已切的旧段(如那 48 段 남성1)仍是 24k —— 要统一可重切(会保留校对文本的话需专门脚本,普通重切会清掉 approve)。
- [x] **Mac 训练教程 `training/GPT_SOVITS_MAC.md`**:Apple Silicon 上装 GPT-SoVITS v2(`install.sh --device MPS`)+ `PYTORCH_ENABLE_MPS_FALLBACK=1` + 接入导出的 zip(相对→绝对路径)+ 跳过自带切分/ASR + 预处理3步 + 训 SoVITS/GPT + 推理 + 排坑表。诚实标注:Mac 训练慢、偶尔排坑,推理没问题,嫌慢就云 GPU 训。
- 下一步:① 真采男/女各 10–60 分钟并 approve;② Mac 上按教程跑 GPT-SoVITS;③ baseline 试听 → 迭代。
- 待定:存量 48 段 남성1 是否重切到 32k(需保文本的专门脚本);切片采样率字段在 manifest 里现统一报 settings 值, 旧 24k 段会被标成 32k(无害, GPT-SoVITS 读 wav 头为准)。
- 注:node 经 nvm 装 v24(本机原无 brew/node),详见下方「会咬人的隐藏知识」与 memory `project_frontend_dev_setup`。

### 多语言支持上线(2026-05-31)
- [x] **从朝鲜语单语转向多语言**:起因是上传中文音频时 worker 硬编码 `language="ko"`,Whisper 被迫把中文按韩语解码、产出以假乱真的韩文。改为**按录音自身语种转写**。
- [x] **数据模型**:`recordings.language`(迁移 `0004`,`server_default=ko` 回填存量韩语行;`Language` StrEnum = en/zh/ko)。`RecordingCreate/Read` 带 `language`;router 建行存语种;worker `_process_segment` 读 `rec.language` 传给 `asr.transcribe`(原全局 `settings.asr_language` 降级为兜底)。
- [x] **设计取舍(joshua 拍板)**:Whisper 识别层无法区分地区变体(英式/美式、普通话/台湾国语、韩国语/朝鲜语 等同 en/zh/ko),故上传只放**三种基础语言**(英语/普通话/朝鲜语,默认朝鲜语);**台湾国语不转繁体**(没引入 opencc)。
- [x] **类别改名**:讲道/圣经朗读/赞美诗 → 播音/演讲/朗诵(仅前端标签 + 列表展示,底层值不变)。
- [x] **前端**:上传页加「语种」药丸选择;采集列表加「语种」列;`types.ts` 加 `Language`。
- [x] **已部署并线上验证**(commit `1383c7b`):`deploy.sh` 重建 backend+worker + `alembic upgrade head`(线上 `alembic current=0004`、`recordings.language` 列在);前端 dist 随 rsync 上线;`/health` 正常。存量 48 段韩语切片不受影响。

### 范文管理上线(2026-06-06)
- [x] **新功能(joshua 提出)**:admin 上传 Word(.docx)范文 → 系统按段落拆成「行/切分」→ admin 编辑 → 定稿。这是与现有「音频→文本」相反的**文本先行**流水线:范文行将来供采集员逐行朗读录音(录音环节是下一阶段,本次未做)。
- [x] **数据模型**(迁移 `0006`):新表 `scripts`(title/language/content_category/notes/status/source_docx_key/created_by) + `script_lines`(script_id/line_index/text,唯一约束 `(script_id,line_index)`)。复用 `Language`/`ContentCategory` 枚举;新 `ScriptStatus` = draft/finalized。**不动现有表**。
- [x] **拆分规则(joshua 拍板)**:python-docx 按段落拆、跳空行、trim;**不做朗读时长校验**。原始 .docx 存 R2(`scripts/{id}/source.docx`)备查。
- [x] **定稿语义(joshua 拍板)**:`finalized` = 标记「可供采集员录音」,但**仍可编辑**(不锁定);`draft` 采集员看不到。语种/类别**整篇统一**。
- [x] **后端**:`app/docx_parse.py`(纯函数 + 单测)、`app/routers/scripts.py`(全 admin-only:上传解析/列表/详情/改属性+定稿/整页存行/删除)。整页保存用 **replace-all**(删旧行按数组重排;⚠️行 id 每次保存会变,等录音环节需要稳定 id 时再改增量)。
- [x] **前端**:`routes/Scripts.tsx`(列表+上传弹窗)+ `routes/ScriptDetail.tsx`(逐行编辑:改/删/增/原生 HTML5 拖拽重排/▲▼移动/拆行/合行 + 整页保存 + 定稿开关)。导航「范文管理」入口仅 admin 可见;`api.ts` 加 multipart(FormData)支持。
- [x] **测试**:docx 解析 4 个单测;`tests/integration/test_scripts.py` 17 个集成测试(本地无 docker/pg 故 skip,靠 CI postgres 跑)。

### 可选下一步(MVP 后)
- 范文录音环节:采集员按定稿范文逐行录音(行→音频关联表;届时 `script_lines` 需稳定 id)
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
