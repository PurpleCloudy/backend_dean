"""Focused human access boundaries on an explicitly leased synthetic database."""
import asyncio
from datetime import datetime
import json
import secrets
from urllib.parse import urlsplit

from argon2 import PasswordHasher
import httpx
import psycopg
from psycopg.rows import dict_row

from test_integration_support import settings, output_directory


async def main():
    env = settings()
    assert env.get('RUN_ACCESS_INTEGRATION_TESTS') == '1', 'Explicit synthetic account fixture lease required'
    dsn = env['TEST_MIGRATION_DATABASE_URL']
    assert urlsplit(dsn).hostname in {'127.0.0.1', 'localhost'}, 'Local synthetic database only'
    proof = {'database': urlsplit(dsn).path.lstrip('/'), 'cases': []}
    path = output_directory(env) / ('access-http-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
    origin = {'Origin': 'http://localhost:5173'}
    password = secrets.token_urlsafe(24)
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as db:
        student = db.execute("SELECT u.user_id,u.login,u.password_hash,u.is_active,s.student_id,s.person_id FROM deanery.app_user u JOIN deanery.app_role r USING(role_id) JOIN deanery.student s USING(person_id) WHERE r.code='student' AND u.is_active ORDER BY u.user_id LIMIT 1").fetchone()
        teacher = db.execute("SELECT u.user_id,u.login,u.password_hash,u.is_active,t.employee_id AS teacher_id FROM deanery.app_user u JOIN deanery.teacher t ON t.employee_id=(SELECT employee_id FROM deanery.employee WHERE person_id=u.person_id) WHERE u.login='novikov'").fetchone()
        staff = db.execute("SELECT user_id,login,password_hash,is_active FROM deanery.app_user WHERE login='popova'").fetchone()
        assert student and teacher and staff, 'Expected supplied synthetic users'
        fixtures = [student, teacher, staff]
        def record(name, expected, actual, checks):
            proof['cases'].append({'id': name, 'expected': expected, 'actual': actual, 'checks': checks, 'status': 'pass' if all(checks.values()) else 'fail'})
            path.write_text(json.dumps(proof, indent=2, default=str))
            assert all(checks.values()), name
        try:
            for actor in fixtures:
                db.execute('UPDATE deanery.app_user SET password_hash=%s WHERE user_id=%s', (PasswordHasher().hash(password), actor['user_id']))
            async with httpx.AsyncClient(base_url=env.get('BASE_URL', 'http://127.0.0.1:8000'), timeout=30, trust_env=False) as http:
                headers = {}
                for name, actor in zip(('student', 'teacher', 'staff'), fixtures):
                    result = await http.post('/api/v1/auth/login', json={'login': actor['login'], 'password': password}, headers=origin)
                    assert result.status_code == 200, result.text
                    headers[name] = {**origin, 'Authorization': 'Bearer ' + result.json()['access_token']}
                peer = db.execute('SELECT student_id FROM deanery.student WHERE student_id<>%s ORDER BY student_id LIMIT 1', (student['student_id'],)).fetchone()['student_id']
                own = await http.get('/api/v1/resources/student', params={'student_id': student['student_id']}, headers=headers['student'])
                other = await http.get('/api/v1/resources/student', params={'student_id': peer}, headers=headers['student'])
                record('student-own-and-peer', {'own_id': student['student_id'], 'peer_rows': 0}, {'own': own.json(), 'peer': other.json()},
                       {'own_visible': own.status_code == 200 and [r['student_id'] for r in own.json()['items']] == [student['student_id']], 'peer_hidden': other.status_code == 200 and other.json()['items'] == []})
                people = await http.get('/api/v1/resources/person', headers=headers['student'])
                private_keys = {'passport_series', 'passport_number', 'snils', 'inn', 'address', 'birth_date'}
                record('public-person-projection', {'private_keys': 'absent'}, {'status': people.status_code, 'keys': sorted(people.json()['items'][0])},
                       {'safe_projection': people.status_code == 200 and all(not private_keys.intersection(r) for r in people.json()['items'])})
                own_private = await http.get(f"/api/v1/people/{student['person_id']}/private", headers=headers['student'])
                denied_private = await http.get(f"/api/v1/people/{student['person_id']}/private", headers=headers['teacher'])
                staff_private = await http.get(f"/api/v1/people/{student['person_id']}/private", headers=headers['staff'])
                record('private-person-human-scope', {'statuses': [200, 403, 200]}, {'statuses': [own_private.status_code, denied_private.status_code, staff_private.status_code]},
                       {'self_and_staff_only': [own_private.status_code, denied_private.status_code, staff_private.status_code] == [200, 403, 200]})
                assigned = db.execute('SELECT s.student_id FROM deanery.student s JOIN deanery.teaching_assignment a USING(group_id) WHERE a.teacher_id=%s ORDER BY s.student_id LIMIT 1', (teacher['teacher_id'],)).fetchone()['student_id']
                unrelated = db.execute('SELECT s.student_id FROM deanery.student s WHERE NOT EXISTS(SELECT 1 FROM deanery.teaching_assignment a WHERE a.group_id=s.group_id AND a.teacher_id=%s) ORDER BY s.student_id LIMIT 1', (teacher['teacher_id'],)).fetchone()['student_id']
                yes = await http.get('/api/v1/resources/student', params={'student_id': assigned}, headers=headers['teacher'])
                no = await http.get('/api/v1/resources/student', params={'student_id': unrelated}, headers=headers['teacher'])
                record('teacher-assignment-boundary', {'own_id': assigned, 'unrelated_id': unrelated, 'unrelated_rows': 0}, {'own': yes.json(), 'unrelated': no.json()},
                       {'assigned_visible': yes.status_code == 200 and [r['student_id'] for r in yes.json()['items']] == [assigned], 'unrelated_hidden': no.status_code == 200 and no.json()['items'] == []})
                before = db.execute('SELECT needs_dormitory FROM deanery.student WHERE student_id=%s', (student['student_id'],)).fetchone()['needs_dormitory']
                denied = await http.patch('/api/v1/resources/student', params={'student_id': student['student_id']}, json={'needs_dormitory': not before}, headers=headers['student'])
                after = db.execute('SELECT needs_dormitory FROM deanery.student WHERE student_id=%s', (student['student_id'],)).fetchone()['needs_dormitory']
                record('student-write-denied', {'status': 403, 'unchanged': True}, {'status': denied.status_code, 'unchanged': before == after}, {'denied': denied.status_code == 403, 'no_effect': before == after})
                db.execute('UPDATE deanery.app_user SET is_active=false WHERE user_id=%s', (student['user_id'],))
                inactive = await http.get('/api/v1/auth/me', headers=headers['student'])
                record('inactive-access-token', {'status': 401}, {'status': inactive.status_code}, {'current_status_checked': inactive.status_code == 401})
        finally:
            for actor in fixtures:
                db.execute('UPDATE deanery.app_user SET password_hash=%s,is_active=%s WHERE user_id=%s', (actor['password_hash'], actor['is_active'], actor['user_id']))
                db.execute('UPDATE backend.sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL', (actor['user_id'],))
    print(json.dumps({'status': 'pass', 'cases': len(proof['cases']), 'evidence': str(path)}))


if __name__ == '__main__':
    asyncio.run(main())
