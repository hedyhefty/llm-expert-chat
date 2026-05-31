"""add model route configs

Revision ID: a4e7d9f12c3b
Revises: de2f19f835ba
Create Date: 2026-05-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4e7d9f12c3b"
down_revision: Union[str, None] = "de2f19f835ba"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_route_configs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("normal_provider_id", sa.String(length=32), nullable=True),
        sa.Column("expert_planner_provider_id", sa.String(length=32), nullable=True),
        sa.Column("expert_provider_ids", sa.Text(), nullable=False),
        sa.Column("expert_reviewer_provider_id", sa.String(length=32), nullable=True),
        sa.Column("expert_synthesizer_provider_id", sa.String(length=32), nullable=True),
        sa.Column("debate_debater_provider_ids", sa.Text(), nullable=False),
        sa.Column("debate_synthesizer_provider_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_model_route_configs_user_id"),
    )
    op.create_index(op.f("ix_model_route_configs_user_id"), "model_route_configs", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_model_route_configs_user_id"), table_name="model_route_configs")
    op.drop_table("model_route_configs")
