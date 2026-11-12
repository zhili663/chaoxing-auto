"""study_runs：刷课控制信号（暂停 / 继续 / 中止）。

一个账号一行（``account_id`` 唯一），状态语义见
``chaoxing/storage/models/study.py`` 里的五个 ``RUN_*`` 常量。
信号必须落在数据库而不是进程内存：CLI 与 API 是两个进程，调度器跑在 API 进程里。

Revision ID: a61dcdcb05af
Revises: 805e0a0fa35b
Create Date: 2026-10-12 14:35:19.511784

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a61dcdcb05af"
down_revision: str | Sequence[str] | None = "805e0a0fa35b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "study_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_study_runs_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_study_runs")),
    )
    with op.batch_alter_table("study_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_study_runs_account_id"), ["account_id"], unique=True)
        batch_op.create_index(batch_op.f("ix_study_runs_state"), ["state"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("study_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_study_runs_state"))
        batch_op.drop_index(batch_op.f("ix_study_runs_account_id"))

    op.drop_table("study_runs")
