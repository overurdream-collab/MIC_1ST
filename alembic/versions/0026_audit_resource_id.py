"""Widen the audit resource identifier for policy fingerprints.

Revision ID: 0026_audit_resource_id
Revises: 0025_claim_batch_display
"""
from alembic import op
import sqlalchemy as sa

revision = "0026_audit_resource_id"
down_revision = "0025_claim_batch_display"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "audit_log",
        "resource_id",
        existing_type=sa.String(36),
        type_=sa.String(128),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        "audit_log",
        "resource_id",
        existing_type=sa.String(128),
        type_=sa.String(36),
        existing_nullable=True,
    )
