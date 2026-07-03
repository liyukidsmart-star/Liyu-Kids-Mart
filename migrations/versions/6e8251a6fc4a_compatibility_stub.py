"""Compatibility stub for missing deployed revision

Revision ID: 6e8251a6fc4a
Revises: f0025676c6cf
Create Date: 2026-07-04 00:05:00.000000

This stub restores the missing Alembic revision identifier referenced by the
deployed database so the app can boot on Vercel again. It intentionally makes
no schema changes.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '6e8251a6fc4a'
down_revision = 'f0025676c6cf'
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
