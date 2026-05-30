"""add recordings.speaker

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-30

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("recordings", sa.Column("speaker", sa.String(length=128), nullable=True))
    op.create_index("ix_recordings_speaker", "recordings", ["speaker"])


def downgrade() -> None:
    op.drop_index("ix_recordings_speaker", table_name="recordings")
    op.drop_column("recordings", "speaker")
