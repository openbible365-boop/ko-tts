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


class RecordingStatus(enum.StrEnum):
    pending_upload = "pending_upload"  # 行已建、预签名 URL 已发, 文件尚未确认入 R2
    uploaded = "uploaded"
    segmenting = "segmenting"
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
    original_filename: Mapped[str | None] = mapped_column(String(512))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[int | None] = mapped_column(Integer)
    codec: Mapped[str | None] = mapped_column(String(32))
    content_category: Mapped[str] = mapped_column(String(32), nullable=False)
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
