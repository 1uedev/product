"""identity infra

Revision ID: 0001
Revises: 
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parent.parent / "sql" / "0001_identity_infra.sql"


def upgrade() -> None:
    # psycopg parses % as a placeholder marker, so literal percent signs in the SQL are doubled
    op.get_bind().exec_driver_sql(SQL_FILE.read_text(encoding="utf-8").replace("%", "%%"))


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations; restore from backup instead")
