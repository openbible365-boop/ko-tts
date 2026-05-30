import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models import ContentCategory, UserRole


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=255)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: str
    display_name: str | None
    is_active: bool
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "UserUpdate":
        if self.role is None and self.is_active is None:
            raise ValueError("must provide at least one of: role, is_active")
        return self


class RecordingCreate(BaseModel):
    content_category: ContentCategory
    original_filename: str = Field(min_length=1, max_length=512)
    mime_type: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=512)
    notes: str | None = None
    remove_music: bool = False  # 切分前剥掉背景音乐(人声分离)


class RecordingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    uploaded_by: uuid.UUID
    audio_key: str
    original_filename: str | None
    mime_type: str | None
    file_size_bytes: int | None
    duration_ms: int | None
    sample_rate: int | None
    channels: int | None
    codec: str | None
    content_category: str
    title: str | None
    notes: str | None
    remove_music: bool
    status: str
    created_at: datetime
    updated_at: datetime


class RecordingCreateResponse(BaseModel):
    recording: RecordingRead
    upload_url: str
    upload_expires_in: int


class RecordingProgress(BaseModel):
    status: str
    remove_music: bool
    segment_total: int
    transcribed: int  # 已转写(过了 ASR)的段数
    in_transcription: int  # 待转写 + 转写中
    phase_elapsed_sec: int  # 当前阶段已用秒数(= now - updated_at)


class PresignedUrlResponse(BaseModel):
    url: str
    expires_in: int


class SegmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recording_id: uuid.UUID
    segment_index: int
    start_ms: int
    end_ms: int
    duration_ms: int
    audio_key: str | None
    asr_text: str | None
    text: str | None
    status: str
    corrected_by: uuid.UUID | None
    corrected_at: datetime | None
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime


class SegmentCorrect(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


class SegmentReject(BaseModel):
    # 从 pending_review 退回时通常带理由; 从 approved 直接退回改可不填
    rejection_reason: str | None = Field(default=None, max_length=1000)


class ExportCategoryStats(BaseModel):
    count: int
    duration_ms: int


class ExportStats(BaseModel):
    total: int
    total_duration_ms: int
    by_status: dict[str, int]
    by_category: dict[str, ExportCategoryStats]
