"""add push_devices

Revision ID: 7e16117eb23a
Revises: feaf615e1b1f
Create Date: 2026-10-03 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7e16117eb23a"
down_revision: Union[str, Sequence[str], None] = "feaf615e1b1f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "push_devices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("fcm_token", sa.String(length=255), nullable=False),
        sa.Column(
            "device_type",
            sa.Enum("android", "gnome", "other", name="devicetype"),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("push_devices", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_push_devices_fcm_token"), ["fcm_token"], unique=True)
        batch_op.create_index(batch_op.f("ix_push_devices_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("push_devices", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_push_devices_user_id"))
        batch_op.drop_index(batch_op.f("ix_push_devices_fcm_token"))

    op.drop_table("push_devices")
