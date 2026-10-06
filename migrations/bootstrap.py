"""Explicit local/administrative provisioning. No usable default credentials."""
import argparse
import os
from getpass import getpass
import psycopg
from psycopg import sql
from argon2 import PasswordHasher

parser=argparse.ArgumentParser()
parser.add_argument('--login',default='admin')
parser.add_argument('--person-id',type=int)
args=parser.parse_args()
password=os.environ.get('BOOTSTRAP_PASSWORD') or getpass('Administrator password (12+ characters): ')
if len(password)<12: raise SystemExit('Password must contain at least 12 characters')
with psycopg.connect(os.environ['MIGRATION_DATABASE_URL']) as conn:
    for role,var in [('deanery_runtime','RUNTIME_PASSWORD'),('deanery_jobs','JOB_PASSWORD'),('deanery_upstream','UPSTREAM_PASSWORD')]:
        value=os.environ.get(var)
        if value:
            if len(value)<20: raise SystemExit(var+' must contain at least 20 characters')
            conn.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(sql.Identifier(role),sql.Literal(value)))
    row=conn.execute("SELECT role_id FROM deanery.app_role WHERE code='admin'").fetchone()
    role_id=row[0] if row else conn.execute("INSERT INTO deanery.app_role(code,name) VALUES('admin','Администратор системы') RETURNING role_id").fetchone()[0]
    person_id=args.person_id
    if person_id is None:
        found=conn.execute('SELECT person_id FROM deanery.app_user WHERE lower(login)=lower(%s)',(args.login,)).fetchone()
        person_id=found[0] if found else conn.execute("INSERT INTO deanery.person(last_name,first_name,gender,birth_date) VALUES('Системный','Администратор','M','1990-01-01') RETURNING person_id").fetchone()[0]
    encoded=PasswordHasher().hash(password)
    row=conn.execute('INSERT INTO deanery.app_user(person_id,role_id,login,password_hash) VALUES(%s,%s,%s,%s) ON CONFLICT(person_id) DO UPDATE SET role_id=excluded.role_id,login=excluded.login,password_hash=excluded.password_hash,is_active=true RETURNING user_id',(person_id,role_id,args.login,encoded)).fetchone()
    conn.execute('UPDATE backend.sessions SET revoked_at=now() WHERE user_id=%s',(row[0],))
print('Administrator provisioned; existing sessions revoked.')
