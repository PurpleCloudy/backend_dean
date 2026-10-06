"""A same-scope proposal remains inspectable/rejectable after its sheet closes."""
import asyncio
from datetime import datetime
import json
from uuid import uuid4
import httpx
import psycopg
from psycopg.rows import dict_row
from test_integration_support import settings, output_directory


async def main():
    env = settings()
    assert env.get('RUN_AGENT_INTEGRATION_TESTS') == '1'
    evidence = {}
    with psycopg.connect(env['TEST_MIGRATION_DATABASE_URL'], autocommit=True, row_factory=dict_row) as db:
        one = lambda q, p=(): db.execute(q, p).fetchone()
        async with httpx.AsyncClient(base_url=env['BASE_URL'], trust_env=False, timeout=60, headers={'Origin': 'http://localhost:5173'}) as teacher, \
                   httpx.AsyncClient(base_url=env['BASE_URL'], trust_env=False, timeout=60, headers={'Origin': 'http://localhost:5173'}) as staff:
            for client, role in ((teacher, 'TEACHER'), (staff, 'STAFF')):
                login = await client.post('/api/v1/auth/login', json={'login': env['TEST_' + role + '_LOGIN'], 'password': env['TEST_' + role + '_PASSWORD']})
                assert login.status_code == 200
                client.headers['Authorization'] = 'Bearer ' + login.json()['access_token']
            proposal_id = env.get('TEST_STALE_PROPOSAL_ID')
            if not proposal_id:
                grade = one("SELECT g.*,gs.group_id FROM deanery.grade g JOIN deanery.grade_sheet gs USING(sheet_id) JOIN deanery.employee e ON e.employee_id=gs.examiner_id JOIN deanery.app_user u USING(person_id) WHERE u.login=%s AND gs.status='open' ORDER BY g.grade_id LIMIT 1", (env['TEST_TEACHER_LOGIN'],))
                assert grade, 'Run agent_flow once to provide the independent open-sheet fixture'
                points = 30 if grade['points'] != 30 else 31
                args = {'sql': f"UPDATE deanery.grade SET points={points} WHERE grade_id={grade['grade_id']}", 'explanation': 'Изменение до закрытия проверочной ведомости', 'reason': 'Проверка отклонения устаревшего предложения'}
                result = await teacher.post('/api/v1/agent/chat', json={'message': 'VERIFY_TOOL:' + json.dumps({'name': 'propose_sql_change', 'arguments': args})})
                assert result.status_code == 200
                proposal_id = json.loads(json.loads(result.json()['answer'])['tool_results'][0])['proposal_id']
                admin = one("SELECT user_id FROM deanery.app_user WHERE login='admin'")['user_id']
                with db.transaction():
                    db.execute("SELECT backend.set_actor(%s,'complete-open-sheet-fixture')", (admin,))
                    db.execute("SELECT backend.command('record_grade')")
                    db.execute('INSERT INTO deanery.grade(sheet_id,student_id,is_absent) SELECT %s,s.student_id,true FROM deanery.student s JOIN deanery.student_status st USING(status_id) WHERE s.group_id=%s AND st.is_active AND NOT EXISTS(SELECT 1 FROM deanery.grade g WHERE g.student_id=s.student_id AND g.sheet_id=%s)', (grade['sheet_id'], grade['group_id'], grade['sheet_id']))
                    db.execute("SELECT backend.set_actor(NULL,'')")
                today = str(one("SELECT (now() AT TIME ZONE 'Europe/Moscow')::date AS day")['day'])
                closed = await staff.post('/api/v1/workflows/close_grade_sheet', json={'sheet_id': grade['sheet_id'], 'closed_date': today}, headers={'Idempotency-Key': str(uuid4())})
                assert closed.status_code == 200, closed.text
                evidence['precondition'] = {'proposal_id': proposal_id, 'grade': grade, 'close_response': closed.json()}
            row = one('SELECT * FROM public.sql_change_proposals WHERE id=%s', (proposal_id,))
            grade_id = row['preview']['before']['grade_id']
            route = '/api/v1/agent/proposals/' + proposal_id
            reviewer = one('SELECT s.employee_id,s.institute_id FROM deanery.dean_office_staff s JOIN deanery.employee e USING(employee_id) JOIN deanery.app_user u USING(person_id) WHERE u.login=%s', (env['TEST_STAFF_LOGIN'],))
            foreign = one('SELECT institute_id FROM deanery.institute WHERE institute_id<>%s ORDER BY institute_id LIMIT 1', (reviewer['institute_id'],))
            admin = one("SELECT user_id FROM deanery.app_user WHERE login='admin'")['user_id']
            db.execute("SELECT backend.set_actor(%s,'stale-review-foreign-control')", (admin,))
            db.execute('UPDATE deanery.dean_office_staff SET institute_id=%s WHERE employee_id=%s', (foreign['institute_id'], reviewer['employee_id']))
            try:
                foreign_before = one('SELECT * FROM deanery.grade WHERE grade_id=%s', (grade_id,))
                audit_before = one('SELECT count(*) AS n FROM deanery.audit_log')['n']
                event_before = one("SELECT count(*) AS n FROM backend.events WHERE action='proposal_decision' AND record_key->>'proposal_id'=%s", (proposal_id,))['n']
                denied_read = await staff.get(route)
                denied_decision = await staff.post(route + '/decision', json={'approve': False, 'reason': 'Недоступное предложение другого института'}, headers={'Idempotency-Key': str(uuid4())})
                control = {'read_status': denied_read.status_code, 'decision_status': denied_decision.status_code,
                    'grade_unchanged': foreign_before == one('SELECT * FROM deanery.grade WHERE grade_id=%s', (grade_id,)),
                    'audit_unchanged': audit_before == one('SELECT count(*) AS n FROM deanery.audit_log')['n'],
                    'events_unchanged': event_before == one("SELECT count(*) AS n FROM backend.events WHERE action='proposal_decision' AND record_key->>'proposal_id'=%s", (proposal_id,))['n']}
                evidence['foreign_control'] = control
            finally:
                db.execute('UPDATE deanery.dean_office_staff SET institute_id=%s WHERE employee_id=%s', (reviewer['institute_id'], reviewer['employee_id']))
                db.execute("SELECT backend.set_actor(NULL,'')")
            before = one('SELECT * FROM deanery.grade WHERE grade_id=%s', (grade_id,))
            audit_before = one('SELECT count(*) AS n FROM deanery.audit_log')['n']
            read = await staff.get(route)
            reason = row['decision_reason'] if row['status'] == 'rejected' else 'Ведомость уже закрыта; предложение отклонено'
            key = row['decision_key'] if row['status'] == 'rejected' else str(uuid4())
            decision = await staff.post(route + '/decision', json={'approve': False, 'reason': reason}, headers={'Idempotency-Key': key})
            after = one('SELECT * FROM deanery.grade WHERE grade_id=%s', (grade_id,))
            audit_after = one('SELECT count(*) AS n FROM deanery.audit_log')['n']
            events = one("SELECT count(*) AS n FROM backend.events WHERE action='proposal_decision' AND record_key->>'proposal_id'=%s", (proposal_id,))['n']
            evidence.update(proposal_id=proposal_id, expected={'read': 200, 'reject': 200, 'domain_effects': 0, 'decision_events': 1},
                actual={'read': {'status': read.status_code, 'body': read.json()}, 'reject': {'status': decision.status_code, 'body': decision.json()},
                        'before': before, 'after': after, 'audit_before': audit_before, 'audit_after': audit_after, 'decision_events': events})
            evidence['pass'] = (read.status_code == decision.status_code == 200 and before == after and audit_before == audit_after and events == 1
                                and control['read_status'] == control['decision_status'] == 404
                                and all(control[name] for name in ('grade_unchanged','audit_unchanged','events_unchanged')))
            path = output_directory(env) / ('stale-proposal-review-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
            path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding='utf8')
            print(json.dumps({'pass': evidence['pass'], 'proposal_id': proposal_id, 'evidence': str(path)}))
            assert evidence['pass'], 'Closed sheet must not prevent an authorized rejection'


if __name__ == '__main__':
    asyncio.run(main())
