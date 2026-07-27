"""voice_models table + default seed

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-27
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_DEFAULT_VOICE_ID = "bP8FJDHmWVEgXJDitdQd"
_DEFAULT_MODEL_ID = "eleven_multilingual_v2"
_DEFAULT_ROW_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def upgrade() -> None:
    op.create_table(
        "voice_models",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("voice_id", sa.String(80), nullable=False),
        sa.Column("model_id", sa.String(80), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO voice_models (id, name, voice_id, model_id, is_default) "
            "VALUES (:id, :name, :voice_id, :model_id, true)"
        ).bindparams(
            id=_DEFAULT_ROW_ID,
            name="Default narrator",
            voice_id=_DEFAULT_VOICE_ID,
            model_id=_DEFAULT_MODEL_ID,
        )
    )


def downgrade() -> None:
    op.drop_table("voice_models")
