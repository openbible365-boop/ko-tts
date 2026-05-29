"""initial schema: users, recordings, segments

Revision ID: 0001
Revises:
Create Date: 2026-05-26

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "recordings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("audio_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("channels", sa.Integer(), nullable=True),
        sa.Column("codec", sa.String(length=32), nullable=True),
        sa.Column("content_category", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"],
            name="fk_recordings_uploaded_by_users", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recordings"),
    )
    op.create_index("ix_recordings_uploaded_by", "recordings", ["uploaded_by"])
    op.create_index("ix_recordings_sha256", "recordings", ["sha256"])
    op.create_index("ix_recordings_status", "recordings", ["status"])

    op.create_table(
        "segments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recording_id", sa.Uuid(), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("audio_key", sa.String(length=512), nullable=True),
        sa.Column("asr_text", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("corrected_by", sa.Uuid(), nullable=True),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"],
            name="fk_segments_recording_id_recordings", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["corrected_by"], ["users.id"],
            name="fk_segments_corrected_by_users", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["users.id"],
            name="fk_segments_reviewed_by_users", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_segments"),
        sa.UniqueConstraint("recording_id", "segment_index", name="uq_segment_recording_index"),
    )
    op.create_index("ix_segments_recording_id", "segments", ["recording_id"])
    op.create_index("ix_segments_status", "segments", ["status"])


def downgrade() -> None:
    op.drop_table("segments")
    op.drop_table("recordings")
    op.drop_table("users")
