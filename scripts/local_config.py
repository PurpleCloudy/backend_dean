"""Generate local-only Compose credentials without changing an existing configuration."""
from pathlib import Path
import json
import secrets
import argparse

parser=argparse.ArgumentParser()
parser.add_argument('--fixture-providers',action='store_true',help='Explicitly configure deterministic local verification providers, not a real model')
args=parser.parse_args()

root = Path(__file__).resolve().parents[1]
target = root / '.env.local'
values = {
    'DEVELOPMENT': 'true',
    'ALLOWED_ORIGINS': '["*"]',
    'POSTGRES_PASSWORD': secrets.token_urlsafe(32),
    'RUNTIME_PASSWORD': secrets.token_urlsafe(32),
    'UPSTREAM_PASSWORD': secrets.token_urlsafe(32),
    'JOB_PASSWORD': secrets.token_urlsafe(32),
    'ACCESS_TOKEN_KEY': secrets.token_urlsafe(48),
    'DELEGATION_SECRET': secrets.token_urlsafe(48),
    'S3_ACCESS_KEY': 'local-' + secrets.token_hex(8),
    'S3_SECRET_KEY': secrets.token_urlsafe(32),
    'S3_ADMIN_ACCESS_KEY': 'local-admin-' + secrets.token_hex(8),
    'S3_ADMIN_SECRET_KEY': secrets.token_urlsafe(32),
    'BOOTSTRAP_PASSWORD': secrets.token_urlsafe(24),
}
if args.fixture_providers:
    values.update(OPENAI_BASE_URL='http://providers:8231/v1',OPENAI_API_KEY='deterministic-local-fixture',OPENAI_MODEL='verification-fixture',BGE_URL='http://providers:8231')
existing = dict(line.split('=', 1) for line in target.read_text(encoding='utf-8').splitlines() if line and not line.startswith('#')) if target.exists() else {}
with target.open('a', encoding='utf-8') as stream:
    for key, value in values.items():
        if key not in existing:
            stream.write(f'{key}={value}\n')
        else:
            values[key] = existing[key]
private = root / '.local'
private.mkdir(exist_ok=True)
(private / 's3.json').write_text(json.dumps({'identities': [
    {'name': 'deanery-runtime', 'credentials': [{'accessKey': values['S3_ACCESS_KEY'], 'secretKey': values['S3_SECRET_KEY']}],
     'actions': ['Read:deanery-private', 'Write:deanery-private', 'List:deanery-private']},
    {'name': 'deanery-bootstrap', 'credentials': [{'accessKey': values['S3_ADMIN_ACCESS_KEY'], 'secretKey': values['S3_ADMIN_SECRET_KEY']}],
     'actions': ['Admin']}
]}), encoding='utf-8')
print(f'Configured {target} and private S3 identities. Existing secrets are preserved.')
if args.fixture_providers:print('LOCAL VERIFICATION: deterministic providers explicitly selected; no real-model quality is claimed.')
