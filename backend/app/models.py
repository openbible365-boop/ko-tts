import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


# 取值集合: 列以 String 存储 (迁移便宜, 加值无需改 DB), 由这些枚举在应用/接口层校验。
class UserRole(enum.StrEnum):
    admin = "admin"
    reviewer = "reviewer"
    contributor = "contributor"


class ContentCategory(enum.StrEnum):
    sermon = "sermon"
    bible_reading = "bible_reading"
    hymn = "hymn"


# 转写语种(基础语言码): Whisper 在识别层面只认基础语言, 无法区分地区变体
# (英式/美式、普通话/台湾国语、韩国语/朝鲜语), 故只存这三种, 直接作为 ASR 语言码。
class Language(enum.StrEnum):
    en = "en"  # 英语
    zh = "zh"  # 普通话/中文
    ko = "ko"  # 朝鲜语/韩语


class RecordingStatus(enum.StrEnum):
    pending_upload = "pending_upload"  # 行已建、预签名 URL 已发, 文件尚未确认入 R2
    pending_separation = "pending_separation"  # 勾了去音乐: 等 worker 做人声分离
    separating = "separating"  # worker 正在去背景音乐(人声分离)
    uploaded = "uploaded"  # 已上传/就绪: 等用户手动「开始切分」
    pending_segmentation = "pending_segmentation"  # 用户已点开始切分, 等 worker 领取
    segmenting = "segmenting"  # worker 正在切分
    segmented = "segmented"
    failed = "failed"


class SegmentStatus(enum.StrEnum):
    pending_transcription = "pending_transcription"
    transcribing = "transcribing"  # worker 正在跑 ASR
    transcription_failed = "transcription_failed"
    pending_correction = "pending_correction"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"


# 范文(供采集员朗读录音的脚本)状态。"定稿后仍可编辑"——finalized 只是
# 标记"可供采集员录音可见", 不锁定内容; draft 则采集员看不到。
class ScriptStatus(enum.StrEnum):
    draft = "draft"  # 草稿: 仅管理员可见, 编辑中
    finalized = "finalized"  # 已定稿: 供采集员录音可见 (仍可被管理员编辑)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=UserRole.contributor)
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Recording(TimestampMixin, Base):
    __tablename__ = "recordings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    audio_key: Mapped[str] = mapped_column(String(512), nullable=False)
    # 去背景音乐后的人声 wav 的 R2 键(分离完成后回填); 切分时若有则用它而非原文件
    processed_audio_key: Mapped[str | None] = mapped_column(String(512))
    original_filename: Mapped[str | None] = mapped_column(String(512))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[int | None] = mapped_column(Integer)
    codec: Mapped[str | None] = mapped_column(String(32))
    content_category: Mapped[str] = mapped_column(String(32), nullable=False)
    # 转写语种(en/zh/ko); 存量录音为韩语, server_default=ko
    language: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="ko", index=True
    )
    title: Mapped[str | None] = mapped_column(String(512))
    notes: Mapped[str | None] = mapped_column(Text)
    # 声音/说话人标注(自由文本, 如 "남성1"/"여성1"/名字), 训练侧按它分组出男/女声
    speaker: Mapped[str | None] = mapped_column(String(128), index=True)
    # 投稿者上传时勾选: 切分前先做人声分离, 剥掉背景音乐
    remove_music: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RecordingStatus.uploaded, index=True
    )

    segments: Mapped[list["Segment"]] = relationship(
        back_populates="recording", cascade="all, delete-orphan"
    )


class Segment(TimestampMixin, Base):
    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("recording_id", "segment_index", name="uq_segment_recording_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    recording_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), index=True, nullable=False
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    audio_key: Mapped[str | None] = mapped_column(String(512))
    asr_text: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SegmentStatus.pending_transcription, index=True
    )
    corrected_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    recording: Mapped["Recording"] = relationship(back_populates="segments")


# === 范文管理 (admin-only): 上传 Word 范文 → 按行拆成待录脚本 → 定稿供采集 ===
class Script(TimestampMixin, Base):
    __tablename__ = "scripts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    # 整篇统一: 语种(en/zh/ko)、内容类别(sermon/bible_reading/hymn), 复用现有枚举
    language: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    content_category: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ScriptStatus.draft, index=True
    )
    # 原始上传的 Word 文件留一份到 R2 备查 (解析后正文已落 script_lines)
    source_docx_key: Mapped[str | None] = mapped_column(String(512))
    original_filename: Mapped[str | None] = mapped_column(String(512))

    lines: Mapped[list["ScriptLine"]] = relationship(
        back_populates="script",
        cascade="all, delete-orphan",
        order_by="ScriptLine.line_index",
    )


class ScriptLine(TimestampMixin, Base):
    __tablename__ = "script_lines"
    __table_args__ = (
        UniqueConstraint("script_id", "line_index", name="uq_script_line_script_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    script_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # 整篇内的行序号(从 0 连续); 整页保存时按数组顺序重排
    line_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    script: Mapped["Script"] = relationship(back_populates="lines")
