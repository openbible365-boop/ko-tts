from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "dev"
    log_level: str = "info"

    # 数据库连接, 形如 postgresql+asyncpg://user:pass@postgres:5432/db
    database_url: str

    # CORS 允许来源, 逗号分隔
    cors_origins: str = ""

    # Cloudflare R2 (S3 兼容)
    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""

    # 鉴权
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 天

    # 限速 (slowapi 字符串语法: "<n>/<period>"; period = second/minute/hour/day)
    # 内存后端单进程 OK; 横向扩展时切 Redis
    rate_limit_login: str = "5/minute"  # 防爆破
    rate_limit_register: str = "3/hour"  # 防批量注册

    # 切分 (worker + ffmpeg silencedetect)
    # 策略: 把语音块合并到 ~target 秒, 并在最近的静音(句末停顿)处下刀;
    # 只有当单段连续语音超过 max 仍无停顿时, 才硬切(兜底)。
    # noise_db=-21 是对真实朗读(约翰福音1)做参数扫描定出来的: 该录音停顿带室
    # 内底噪、从不真正静音, -30dB 几乎扫不到停顿; -21dB/0.25s 能稳定捕捉句末停顿,
    # 切出 ~14s(11~17s)、刀刀落在停顿上的片段。换录音源/麦克风环境可能要重调。
    seg_silence_noise_db: float = -21.0  # 静音判定阈值 (dB), 越接近 0 越宽松
    seg_min_silence_sec: float = 0.25  # 最短静音时长
    seg_min_segment_sec: float = 3.0  # 丢弃短于此的片段
    seg_target_segment_sec: float = 13.0  # 目标片段时长, 达到后在下一处停顿断开
    seg_max_segment_sec: float = 18.0  # 硬上限: 无停顿时超过此长度才硬切
    # 32kHz: GPT-SoVITS 内部即 32k, 取之为训练级保真上限(再高它也会降采样)。
    # 改此值只影响之后新切的段; 已切的旧段仍是当时的采样率。
    seg_sample_rate: int = 32000  # 切片输出采样率 (单声道 wav)

    # 人声分离 (audio-separator, 独立 venv, 仅当 recording.remove_music=True 时启用)
    sep_model_filename: str = "UVR-MDX-NET-Voc_FT.onnx"
    sep_model_dir: str = "/models/audio-separator"  # 模型缓存(worker_models volume)
    worker_poll_interval_sec: float = 5.0  # worker 空闲轮询间隔

    # ASR (faster-whisper)
    # medium: small 在"提词偏置"下会把没声音/听不清的也照抄范文文字 -> 误判通过,
    # 故保留 medium 保证 QC 正确性(速度问题靠硬件升级解决)。
    asr_model_size: str = "medium"  # tiny/base/small/medium/large-v3
    asr_compute_type: str = "int8"  # CTranslate2 量化类型 (CPU 推荐 int8)
    asr_language: str = "ko"  # 强制语言, 提高 Korean 准确率
    asr_beam_size: int = 5
    asr_model_cache_dir: str = "/models"  # 容器内模型缓存目录, 走 named volume

    # reaper: 回收陈旧的 worker claim (segmenting / transcribing 卡死)
    # 注: 开了 remove_music 的录音, 切分阶段含人声分离(~2.2× 实时, 12 分钟录音
    # ~28 分钟), 故 timeout 要足够大, 否则 reaper 会在分离途中把它退回造成反复重切。
    worker_claim_timeout_min: int = 120  # 超过此时长仍在中间状态, 视为陈旧 -> 回退
    worker_reap_interval_sec: float = 300.0  # worker 主循环里的 reap 周期 (5 min)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
