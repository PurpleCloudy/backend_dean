"""Small real-HTTP deployment smoke. Credentials never enter its output."""
import argparse
import json
import os
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--login', default='admin')
    parser.add_argument('--output', default=str(ROOT/'.local/test-results/http-smoke.json'))
    args = parser.parse_args()
    env = {}
    local = ROOT/'.env.local'
    if local.exists():
        env = dict(line.split('=',1) for line in local.read_text(encoding='utf8').splitlines()
                   if line and not line.startswith('#'))
    password = os.environ.get('SMOKE_PASSWORD') or os.environ.get('BOOTSTRAP_PASSWORD') or env.get('BOOTSTRAP_PASSWORD')
    if not password: raise SystemExit('Set SMOKE_PASSWORD or create the local bootstrap configuration')
    rows = []
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    def check(name, condition, actual):
        rows.append({'case':name,'status':'pass' if condition else 'fail','actual':actual})
        if not condition: raise AssertionError(name)
    started = time.monotonic()
    try:
        with httpx.Client(base_url=args.url, timeout=30) as client:
            for endpoint in ('/health/core','/health/ready'):
                r = client.get(endpoint)
                check(endpoint,r.status_code==200,{'status':r.status_code,'body':r.json()})
            doc = client.get('/openapi.json')
            check('OpenAPI',doc.status_code==200 and bool(doc.json().get('paths')),
                  {'status':doc.status_code,'paths':len(doc.json().get('paths',{}))})
            denied = client.get('/api/v1/resources/student')
            check('Anonymous denied',denied.status_code==401,{'status':denied.status_code})
            origin = {'Origin':'http://localhost:5173'}
            login = client.post('/api/v1/auth/login',json={'login':args.login,'password':password},headers=origin)
            check('Login',login.status_code==200 and 'access_token' in login.json(),{'status':login.status_code})
            auth = login.json()
            headers = {'Authorization':'Bearer '+auth['access_token']}
            me = client.get('/api/v1/auth/me',headers=headers)
            check('Authenticated identity',me.status_code==200,{'status':me.status_code})
            schema = client.get('/api/v1/schema',headers=headers)
            check('Published schema',schema.status_code==200 and 'grade_correction' in schema.text
                  and 'v_pending_orders' in schema.text,{'status':schema.status_code,'new_resources_present':
                    all(x in schema.text for x in ('grade_correction','v_pending_orders'))})
            for resource in ('student','grade_correction','v_pending_orders'):
                r = client.get('/api/v1/resources/'+resource,params={'limit':2},headers=headers)
                body = r.json()
                check('Read '+resource,r.status_code==200 and isinstance(body.get('items'),list),
                      {'status':r.status_code,'row_count':len(body.get('items',[]))})
            report = client.get('/api/v1/reports/grades',params={'limit':2},headers=headers)
            check('Report',report.status_code==200 and isinstance(report.json().get('items'),list),
                  {'status':report.status_code,'row_count':len(report.json().get('items',[]))})
            refreshed = client.post('/api/v1/auth/refresh',json={},headers={**origin,'X-CSRF-Token':auth['csrf_token']})
            check('Refresh',refreshed.status_code==200,{'status':refreshed.status_code})
            headers={'Authorization':'Bearer '+refreshed.json()['access_token']}
            logout = client.post('/api/v1/auth/logout',headers=headers,json={})
            check('Logout',logout.status_code in (200,204),{'status':logout.status_code})
            revoked = client.get('/api/v1/auth/me',headers=headers)
            check('Logged-out token denied',revoked.status_code==401,{'status':revoked.status_code})
    except Exception as exc:
        if not rows or rows[-1]['status']!='fail':
            rows.append({'case':'HTTP smoke execution','status':'fail','error_type':type(exc).__name__})
    result={'url':args.url,'elapsed_seconds':round(time.monotonic()-started,3),
            'status':'pass' if rows and all(r['status']=='pass' for r in rows) else 'fail','checks':rows}
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'status':result['status'],'checks':len(rows),'elapsed_seconds':result['elapsed_seconds']}))
    raise SystemExit(0 if result['status']=='pass' else 1)

if __name__=='__main__':main()
