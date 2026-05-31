"""add recordings.language

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-31

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 存量录音均为韩语内容, server_default=ko 回填既有行。
    op.add_column(
        "recordings",
        sa.Column(
            "language",
            sa.String(length=16),
            nullable=False,
            server_default="ko",
        ),
    )
    op.create_index("ix_recordings_language", "recordings", ["language"])


def downgrade() -> None:
    op.drop_index("ix_recordings_language", table_name="recordings")
    op.drop_column("recordings", "language")
