"""Add mini-app visibility flags to loyalty settings

Revision ID: fe7e7d1f4b22
Revises: 8f1b2e3c4d5a
Create Date: 2026-07-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'fe7e7d1f4b22'
down_revision = '8f1b2e3c4d5a'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col['name'] for col in inspector.get_columns('loyalty_settings')}
    
    with op.batch_alter_table('loyalty_settings', schema=None) as batch_op:
        if 'show_categories_in_mini_app' not in columns:
            batch_op.add_column(sa.Column('show_categories_in_mini_app', sa.Boolean(), nullable=False, server_default=sa.true()))
        if 'show_age_filter_in_mini_app' not in columns:
            batch_op.add_column(sa.Column('show_age_filter_in_mini_app', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    with op.batch_alter_table('loyalty_settings', schema=None) as batch_op:
        batch_op.drop_column('show_age_filter_in_mini_app')
        batch_op.drop_column('show_categories_in_mini_app')
