"""Bounded real HTTP auth and request-boundary checks; uses separate sessions."""
import asyncio
import json
from datetime import datetime, timezone

import httpx

from test_integration_support import settings, output_directory


async def main():
    env = settings()
    url = env.get('BASE_URL', 'http://127.0.0.1:8000')
    origin = 'http://localhost:5173'
    login = {'login': env.get('TEST_ADMIN_LOGIN', 'admin'),
             'password': env.get('TEST_ADMIN_PASSWORD', env.get('BOOTSTRAP_PASSWORD'))}
    assert login['password'], 'Explicit local test password required'
    cases = []
    target = output_directory(env) / ('auth-http-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')

    def check(name, expected, actual, assertions):
        cases.append({'id': name, 'expected': expected, 'actual': actual,
                      'assertions': assertions, 'status': 'pass' if all(assertions.values()) else 'fail'})
        target.write_text(json.dumps({'started_at': started, 'base_url': url, 'cases': cases}, indent=2))
        assert all(assertions.values()), name

    started = datetime.now(timezone.utc).isoformat()
    async with httpx.AsyncClient(base_url=url, timeout=20, trust_env=False) as client:
        bad_origin = await client.post('/api/v1/auth/login', json=login, headers={'Origin': 'https://outside.invalid'})
        check('login-origin', {'status': 403}, {'status': bad_origin.status_code}, {'denied': bad_origin.status_code == 403})
        docs_login = await client.post('/api/v1/auth/login', json=login, headers={'Origin': 'http://localhost:8000'})
        check('local-swagger-origin', {'status': 200}, {'status': docs_login.status_code}, {'accepted': docs_login.status_code == 200})
        await client.post('/api/v1/auth/logout', json={}, headers={'Authorization': 'Bearer ' + docs_login.json()['access_token']})
        missing = await client.get('/api/v1/auth/me')
        forged = await client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer invalid', 'X-User-ID': '1', 'X-Role': 'admin'})
        check('unauthorized-and-spoofed-identity', {'statuses': [401, 401]}, {'statuses': [missing.status_code, forged.status_code]},
              {'both_denied': missing.status_code == forged.status_code == 401})
        admitted = await client.post('/api/v1/auth/login', json=login, headers={'Origin': origin})
        assert admitted.status_code == 200, admitted.text
        session = admitted.json()
        cookie = '; '.join(f'{name}={value}' for name, value in client.cookies.items())
        absent_csrf = await client.post('/api/v1/auth/refresh', json={}, headers={'Origin': origin})
        check('refresh-csrf', {'status': 403}, {'status': absent_csrf.status_code}, {'denied': absent_csrf.status_code == 403})
        async def refresh():
            async with httpx.AsyncClient(base_url=url, timeout=20, trust_env=False) as isolated:
                return await isolated.post('/api/v1/auth/refresh', json={}, headers={
                    'Origin': origin, 'X-CSRF-Token': session['csrf_token'], 'Cookie': cookie})
        simultaneous = await asyncio.gather(refresh(), refresh())
        winner = next((r for r in simultaneous if r.status_code == 200), None)
        statuses = sorted(r.status_code for r in simultaneous)
        reused = await client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + (winner.json()['access_token'] if winner else session['access_token'])})
        check('concurrent-refresh-reuse', {'statuses': [200, 401], 'winner_token_after_reuse': 401},
              {'statuses': statuses, 'winner_token_after_reuse': reused.status_code},
              {'single_rotation': statuses == [200, 401], 'family_revoked': reused.status_code == 401})
        preflight = await client.options('/api/v1/resources/student', headers={'Origin': 'https://outside.invalid',
                                         'Access-Control-Request-Method': 'GET'})
        check('cors-untrusted-origin', {'status': 400, 'allow_origin': None},
              {'status': preflight.status_code, 'allow_origin': preflight.headers.get('access-control-allow-origin')},
              {'denied': preflight.status_code == 400, 'not_allowed': 'access-control-allow-origin' not in preflight.headers})
        malformed = await client.post('/api/v1/auth/login', content=b'{', headers={'Origin': origin, 'Content-Type': 'application/json'})
        declared = await client.post('/api/v1/auth/login', content=b'x' * 65537, headers={'Origin': origin, 'Content-Type': 'application/json'})
        async def oversized():
            yield b'x' * 32768
            yield b'x' * 32769
        chunked = await client.post('/api/v1/auth/login', content=oversized(), headers={'Origin': origin, 'Content-Type': 'application/json'})
        check('malformed-and-body-cap', {'statuses': [400, 413, 413]},
              {'statuses': [malformed.status_code, declared.status_code, chunked.status_code]},
              {'safe_parse_error': malformed.status_code == 400, 'declared_limit': declared.status_code == 413, 'chunked_limit': chunked.status_code == 413})
    print(json.dumps({'status': 'pass', 'cases': len(cases), 'evidence': str(target)}))


if __name__ == '__main__':
    asyncio.run(main())
