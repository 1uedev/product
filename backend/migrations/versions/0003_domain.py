"""domain

Revision ID: 0003
Revises: 0002
"""
from pathlib import Path

from alembic import op

revision = "0003"
down_revision = '0002'
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parent.parent / "sql" / "0003_domain.sql"


def upgrade() -> None:
    # psycopg parses % as a placeholder marker, so literal percent signs in the SQL are doubled
    op.get_bind().exec_driver_sql(SQL_FILE.read_text(encoding="utf-8").replace("%", "%%"))


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations; restore from backup instead")
