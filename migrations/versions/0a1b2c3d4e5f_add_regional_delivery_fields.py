"""Add regional delivery fields and TeleBirr settings

Revision ID: 0a1b2c3d4e5f
Revises: b1c2d3e4f5a6
Create Date: 2026-07-19 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0a1b2c3d4e5f'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    address_columns = {col['name'] for col in inspector.get_columns('addresses')}
    loyalty_columns = {col['name'] for col in inspector.get_columns('loyalty_settings')}

    with op.batch_alter_table('addresses', schema=None) as batch_op:
        if 'region' not in address_columns:
            batch_op.add_column(sa.Column('region', sa.String(length=100), nullable=True))
        if 'city_town' not in address_columns:
            batch_op.add_column(sa.Column('city_town', sa.String(length=100), nullable=True))
        if 'delivery_scope' not in address_columns:
            batch_op.add_column(sa.Column('delivery_scope', sa.String(length=20), nullable=False, server_default='addis'))

    with op.batch_alter_table('loyalty_settings', schema=None) as batch_op:
        if 'telebirr_payment_phone' not in loyalty_columns:
            batch_op.add_column(sa.Column('telebirr_payment_phone', sa.String(length=32), nullable=False, server_default=''))


def downgrade():
    with op.batch_alter_table('loyalty_settings', schema=None) as batch_op:
        batch_op.drop_column('telebirr_payment_phone')

    with op.batch_alter_table('addresses', schema=None) as batch_op:
        batch_op.drop_column('delivery_scope')
        batch_op.drop_column('city_town')
        batch_op.drop_column('region')
