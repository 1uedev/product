"""app core

Revision ID: 0002
Revises: 0001
"""
from pathlib import Path

from alembic import op

revision = "0002"
down_revision = '0001'
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parent.parent / "sql" / "0002_app_core.sql"


def upgrade() -> None:
    # psycopg parses % as a placeholder marker, so literal percent signs in the SQL are doubled
    op.get_bind().exec_driver_sql(SQL_FILE.read_text(encoding="utf-8").replace("%", "%%"))


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations; restore from backup instead")
