"""recordings.script_id for script-based recording samples

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-06

新增 recordings.script_id (非空=录音样品, 基于定稿范文逐行录音);
唯一约束 (script_id, uploaded_by) 保证每个采集员每篇范文至多一份(可续录)。
status/segment status 是 String 列, 新增取值无需 DDL。

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("recordings", sa.Column("script_id", sa.Uuid(), nullable=True))
    op.create_index("ix_recordings_script_id", "recordings", ["script_id"])
    op.create_foreign_key(
        "fk_recordings_script_id_scripts",
        "recordings",
        "scripts",
        ["script_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # NULL script_id 的多行不冲突(Postgres NULL 视为互不相等), 上传音频不受影响
    op.create_unique_constraint(
        "uq_recording_script_uploader", "recordings", ["script_id", "uploaded_by"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_recording_script_uploader", "recordings", type_="unique")
    op.drop_constraint("fk_recordings_script_id_scripts", "recordings", type_="foreignkey")
    op.drop_index("ix_recordings_script_id", table_name="recordings")
    op.drop_column("recordings", "script_id")
