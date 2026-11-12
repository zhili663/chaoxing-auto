"""users：可空唯一的手机号列（手机号登录的地基）。

手机号是**可选**的第二个登录标识：没填就是 ``NULL``（SQLite / Postgres 都允许多个
NULL 共存），因此唯一索引既能挡住"两个账号占同一号"，又不会挡住"很多人没填手机号"。

Revision ID: 333ae8dd4493
Revises: a61dcdcb05af
Create Date: 2026-10-13 20:28:18.737061

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "333ae8dd4493"
down_revision: str | Sequence[str] | None = "a61dcdcb05af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("phone", sa.String(length=20), nullable=True))
        batch_op.create_index(batch_op.f("ix_users_phone"), ["phone"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_phone"))
        batch_op.drop_column("phone")
