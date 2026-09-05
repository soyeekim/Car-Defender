"""video playback key

브라우저가 원본을 못 열 때만 채워지는 재생용 H.264 사본의 스토리지 키.
비어 있으면 원본을 그대로 재생한다 — 기존 행은 그대로 두면 된다.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-05

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: str | Sequence[str] | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('playback_key', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.drop_column('playback_key')
