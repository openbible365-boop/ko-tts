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

    # 切分 (worker + ffmpeg silencedetect)
    seg_silence_noise_db: float = -30.0  # 静音判定阈值 (dB)
    seg_min_silence_sec: float = 0.5  # 最短静音时长
    seg_min_segment_sec: float = 1.0  # 丢弃短于此的片段
    seg_max_segment_sec: float = 15.0  # 长于此的片段再切
    seg_sample_rate: int = 24000  # 切片输出采样率 (单声道 wav)
    worker_poll_interval_sec: float = 5.0  # worker 空闲轮询间隔

    # ASR (faster-whisper)
    asr_model_size: str = "medium"  # tiny/base/small/medium/large-v3
    asr_compute_type: str = "int8"  # CTranslate2 量化类型 (CPU 推荐 int8)
    asr_language: str = "ko"  # 强制语言, 提高 Korean 准确率
    asr_beam_size: int = 5
    asr_model_cache_dir: str = "/models"  # 容器内模型缓存目录, 走 named volume

    # reaper: 回收陈旧的 worker claim (segmenting / transcribing 卡死)
    worker_claim_timeout_min: int = 30  # 超过此时长仍在中间状态, 视为陈旧 -> 回退
    worker_reap_interval_sec: float = 300.0  # worker 主循环里的 reap 周期 (5 min)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
