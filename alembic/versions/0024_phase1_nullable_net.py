"""Allow phase 1 imports where the source net is derivable from references.

Revision ID: 0024_phase1_nullable_net
Revises: 0023_insurer_intake
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_phase1_nullable_net"
down_revision = "0023_insurer_intake"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("claim_entries", "claimed_amount",
                    existing_type=sa.Numeric(18, 4), nullable=True)


def downgrade():
    op.execute(sa.text("UPDATE claim_entries SET claimed_amount = 0 WHERE claimed_amount IS NULL"))
    op.alter_column("claim_entries", "claimed_amount",
                    existing_type=sa.Numeric(18, 4), nullable=False)
