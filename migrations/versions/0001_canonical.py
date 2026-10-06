"""Install the single canonical deanery schema on a new database."""
from pathlib import Path
from alembic import op
revision='0001'
down_revision=None


def upgrade():
    conn=op.get_bind()
    present=conn.exec_driver_sql("SELECT to_regnamespace('deanery')").scalar()
    if present:
        raise RuntimeError('A clean database is required; the canonical schema already exists')
    base=Path(__file__).resolve().parents[2]/'sources'/'deanery_db'
    schema=(base/'01_schema.sql').read_text(encoding='utf-8').replace('DROP SCHEMA IF EXISTS deanery CASCADE;','')
    conn.exec_driver_sql(schema)
    conn.exec_driver_sql((base/'02_views_functions.sql').read_text(encoding='utf-8'))


def downgrade():
    raise RuntimeError('No destructive downgrade; restore a verified backup')
