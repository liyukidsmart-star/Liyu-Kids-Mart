"""Fix qty_discount_open_to_all to True for all customers

Revision ID: b1c2d3e4f5a6
Revises: ad44106346be
Create Date: 2026-07-16 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b1c2d3e4f5a6'
down_revision = 'ad44106346be'
branch_labels = None
depends_on = None


def upgrade():
    # Ensure all existing loyalty_settings rows have qty_discount_open_to_all = True
    # This corrects the server_default='false' bug from the previous migration.
    op.execute("UPDATE loyalty_settings SET qty_discount_open_to_all = TRUE WHERE qty_discount_open_to_all = FALSE OR qty_discount_open_to_all IS NULL")


def downgrade():
    pass
