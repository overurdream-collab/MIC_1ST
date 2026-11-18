"""Add human-readable batch name and entry date.

Revision ID: 0025_claim_batch_display
Revises: 0024_phase1_nullable_net
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_claim_batch_display"
down_revision = "0024_phase1_nullable_net"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("claim_batches", sa.Column("display_name", sa.String(200), nullable=True))
    op.add_column("claim_batches", sa.Column("entry_date", sa.Date(), nullable=True))
    op.execute(sa.text(
        "UPDATE claim_batches b "
        "SET display_name = COALESCE(NULLIF(regexp_replace(d.filename, '\\.[^.]+$', ''), ''), "
        "'دفعة ' || left(b.id, 8)), entry_date = b.created_at::date "
        "FROM ingestion_documents d WHERE d.id = b.ingestion_document_id"
    ))
    op.execute(sa.text(
        "UPDATE claim_batches SET display_name = 'دفعة ' || left(id, 8) "
        "WHERE display_name IS NULL OR display_name = ''"
    ))
    op.execute(sa.text(
        "UPDATE claim_batches SET entry_date = created_at::date WHERE entry_date IS NULL"
    ))
    op.alter_column("claim_batches", "display_name", existing_type=sa.String(200), nullable=False)
    op.alter_column("claim_batches", "entry_date", existing_type=sa.Date(), nullable=False)


def downgrade():
    op.drop_column("claim_batches", "entry_date")
    op.drop_column("claim_batches", "display_name")
