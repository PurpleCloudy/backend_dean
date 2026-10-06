"""Run the supplied SQL smoke once on an isolated, freshly seeded source DB.

The fixture database must have only canonical schema/seed, before the app overlay.
No SQL assertion is rewritten or expanded into a role/action matrix.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    if not (args.database.startswith('deanery_smoke_') or args.database == 'deanery_v2_core'):
        raise SystemExit('Requires a dedicated source-smoke database, never the app or historical database')
    values = dict(line.split('=', 1) for line in (ROOT/'.env.local').read_text(encoding='utf8').splitlines()
                  if line and not line.startswith('#'))
    source = ROOT/'sources/deanery_db'
    result_dir = ROOT/'.local/test-results'
    result_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    notices = []
    result = {'database':args.database, 'host':'127.0.0.1', 'port':55433,
              'source_hashes':{name:hashlib.sha256((source/name).read_bytes()).hexdigest()
                  for name in ('01_schema.sql','02_views_functions.sql','03_seed.sql','05_roles_security.sql','06_tests.sql')}}
    with psycopg.connect(host='127.0.0.1', port=55433, dbname=args.database,
                         user='postgres', password=values['POSTGRES_PASSWORD'], autocommit=True) as conn:
        conn.add_notice_handler(lambda d:notices.append(d.message_primary))
        if conn.execute("SELECT to_regnamespace('backend')").fetchone()[0] is not None:
            raise SystemExit('Source smoke must run before the backend overlay')
        names = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='deanery' ORDER BY tablename")]
        before = {name:conn.execute(sql.SQL('SELECT count(*) FROM deanery.{}').format(sql.Identifier(name))).fetchone()[0] for name in names}
        # Native source roles are fixture-only. Precreate NOLOGIN so running the
        # unchanged supplied grants never creates its example-password login.
        for name in ('deanery_read','deanery_teacher','deanery_staff','deanery_agent'):
            if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(name,)).fetchone():
                conn.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(name)))
        conn.execute((source/'05_roles_security.sql').read_text(encoding='utf8'))
        notices.clear()
        suite = (source/'06_tests.sql').read_text(encoding='utf8')
        expected = len(re.findall(r'^SELECT pg_temp\.expect_\w+\(',suite,re.M))
        try:
            conn.execute('\n'.join(line for line in suite.splitlines() if not line.startswith('\\echo')))
            result['status']='pass'
        except psycopg.Error as exc:
            conn.execute('ROLLBACK')
            result.update(status='fail', sqlstate=exc.sqlstate, error=exc.diag.message_primary)
        after = {name:conn.execute(sql.SQL('SELECT count(*) FROM deanery.{}').format(sql.Identifier(name))).fetchone()[0] for name in names}
        result.update(expected_assertions=expected, passed_assertions=sum(n.startswith('✓') for n in notices),
                      notices=notices, row_counts_before=before, row_counts_after=after, rows_unchanged=before==after,
                      duration_seconds=round(time.time()-started,3))
        if result['passed_assertions'] != expected or before != after: result['status']='fail'
    target = result_dir/'source-smoke.json'
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'notices','row_counts_before','row_counts_after','source_hashes'}},ensure_ascii=False))
    raise SystemExit(0 if result['status']=='pass' else 1)

if __name__=='__main__':main()
