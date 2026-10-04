"""rls grants

Revision ID: 0004
Revises: 0003
"""
from pathlib import Path

from alembic import op

revision = "0004"
down_revision = '0003'
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parent.parent / "sql" / "0004_rls_grants.sql"


def upgrade() -> None:
    # psycopg parses % as a placeholder marker, so literal percent signs in the SQL are doubled
    op.get_bind().exec_driver_sql(SQL_FILE.read_text(encoding="utf-8").replace("%", "%%"))


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations; restore from backup instead")
