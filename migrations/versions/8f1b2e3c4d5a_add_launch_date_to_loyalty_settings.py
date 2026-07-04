"""Add launch date to loyalty settings

Revision ID: 8f1b2e3c4d5a
Revises: f0025676c6cf
Create Date: 2026-07-03 23:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8f1b2e3c4d5a'
down_revision = '6e8251a6fc4a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('loyalty_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('launch_date', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('loyalty_settings', schema=None) as batch_op:
        batch_op.drop_column('launch_date')
