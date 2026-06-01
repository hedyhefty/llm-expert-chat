"""add message status

Revision ID: b1f3a7c9e2d4
Revises: a4e7d9f12c3b
Create Date: 2026-06-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1f3a7c9e2d4"
down_revision: Union[str, None] = "a4e7d9f12c3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="completed"),
    )
    op.alter_column("messages", "status", server_default=None)


def downgrade() -> None:
    op.drop_column("messages", "status")
