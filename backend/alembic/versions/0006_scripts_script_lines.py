"""scripts and script_lines (范文管理)

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-06

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 范文: 管理员上传的 Word 拆成的待录脚本(整篇统一语种/类别)
    op.create_table(
        "scripts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("content_category", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_docx_key", sa.String(length=512), nullable=True),
        sa.Column("original_filename", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name="fk_scripts_created_by_users", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scripts"),
    )
    op.create_index("ix_scripts_created_by", "scripts", ["created_by"])
    op.create_index("ix_scripts_language", "scripts", ["language"])
    op.create_index("ix_scripts_status", "scripts", ["status"])

    # 范文的行(切分): 一行 = 一个待录脚本句
    op.create_table(
        "script_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("script_id", sa.Uuid(), nullable=False),
        sa.Column("line_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["script_id"], ["scripts.id"],
            name="fk_script_lines_script_id_scripts", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_script_lines"),
        sa.UniqueConstraint("script_id", "line_index", name="uq_script_line_script_index"),
    )
    op.create_index("ix_script_lines_script_id", "script_lines", ["script_id"])


def downgrade() -> None:
    op.drop_table("script_lines")
    op.drop_table("scripts")
