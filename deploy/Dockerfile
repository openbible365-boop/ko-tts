# syntax=docker/dockerfile:1
# 后端镜像 — 多阶段构建。
# build context = backend/ (见 docker-compose.prod.yml 的 build.context)

# ---------- 阶段 1: 用 uv 安装依赖到 /app/.venv ----------
FROM python:3.12-slim-bookworm AS builder

# uv 官方静态二进制 (生产建议把 :latest 换成固定版本以保证可复现)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# 仅依赖清单先入层, 改代码不必重装依赖
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ---------- 阶段 1b: 人声分离 venv (audio-separator, CPU-only torch) ----------
# 独立 venv, 不进 app 的 uv.lock。torch 用 CPU 轮子避免 CUDA 巨胖。diffq(demucs
# 依赖)要编译, 故本阶段装 build-essential —— 但只在 builder, runtime 不带。
FROM python:3.12-slim-bookworm AS sepbuilder
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/sepvenv \
    && /opt/sepvenv/bin/pip install --no-cache-dir --upgrade pip
# 先装 CPU torch, audio-separator 便不会再拉 CUDA 版
RUN /opt/sepvenv/bin/pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu
RUN /opt/sepvenv/bin/pip install --no-cache-dir "audio-separator[cpu]"

# ---------- 阶段 2: 运行时, 只带 .venv + 应用代码 ----------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# ffmpeg/ffprobe: worker 切分音频用; libsndfile1: audio-separator(soundfile)读写音频
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 非 root 运行用户
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /models && chown app:app /models

WORKDIR /app

# 先放应用代码, 再用 builder 的 .venv 覆盖 (避免 context 里残留的 .venv 干扰)
COPY --chown=app:app . /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
# 人声分离 venv (root 拥有, app 可执行); 仅 remove_music 录音的子进程用
COPY --from=sepbuilder /opt/sepvenv /opt/sepvenv

USER app

EXPOSE 8000

# 不依赖 curl, 用 venv 里的 python 探活
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
