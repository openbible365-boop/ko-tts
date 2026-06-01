"""add recordings.processed_audio_key

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-31

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 去背景音乐后的人声 wav 的 R2 键; 既有行为 NULL(无害, 切分回退到原文件)。
    op.add_column(
        "recordings",
        sa.Column("processed_audio_key", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recordings", "processed_audio_key")
