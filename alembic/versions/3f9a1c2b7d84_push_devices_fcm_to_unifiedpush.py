"""push_devices: replace fcm_token with UnifiedPush endpoint

Morph the push_devices table from the (never-activated) FCM design to UnifiedPush: drop the
FCM-specific columns (fcm_token + its unique index, device_type, label) and add a single
`endpoint` column (the UnifiedPush endpoint URL, unique + indexed). The table carries no
production data (FCM push was never enabled), so no data migration is needed.

Revision ID: 3f9a1c2b7d84
Revises: 7e16117eb23a
Create Date: 2026-10-03 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3f9a1c2b7d84"
down_revision: Union[str, Sequence[str], None] = "7e16117eb23a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("push_devices", schema=None) as batch_op:
        batch_op.add_column(sa.Column("endpoint", sa.String(length=2048), nullable=False))
        batch_op.drop_index(batch_op.f("ix_push_devices_fcm_token"))
        batch_op.drop_column("fcm_token")
        batch_op.drop_column("device_type")
        batch_op.drop_column("label")
        batch_op.create_index(batch_op.f("ix_push_devices_endpoint"), ["endpoint"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("push_devices", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_push_devices_endpoint"))
        batch_op.add_column(sa.Column("fcm_token", sa.String(length=255), nullable=False))
        batch_op.add_column(
            sa.Column(
                "device_type",
                sa.Enum("android", "gnome", "other", name="devicetype"),
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("label", sa.String(length=255), nullable=True))
        batch_op.drop_column("endpoint")
        batch_op.create_index(batch_op.f("ix_push_devices_fcm_token"), ["fcm_token"], unique=True)
