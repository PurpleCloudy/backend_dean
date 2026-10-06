"""Refresh checked-in derived view column types from the synthetic migrated database."""
import json,os
from pathlib import Path
import psycopg

root=Path(__file__).resolve().parents[1]
with psycopg.connect(os.environ['MIGRATION_DATABASE_URL']) as conn:
    rows=conn.execute(r"SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='deanery' AND table_name LIKE 'v\_%' ORDER BY table_name,ordinal_position").fetchall()
data={}
for table,column,kind,nullable in rows: data.setdefault(table,{})[column]={'type':kind,'nullable':nullable=='YES'}
(root/'migrations/view_columns.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
print('Captured',len(data),'view contracts')
