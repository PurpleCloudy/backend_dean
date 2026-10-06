"""Opt-in real HTTP proof against the new seeded, isolated local database.

Requires original agent + deterministic local protocol provider, selected active
TEST_* accounts/passwords, and a leased synthetic TEST_MIGRATION_DATABASE_URL.
It preserves the synthetic mutations and their audit as test evidence.
"""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
import sys
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row

from test_integration_support import ROOT, output_directory, settings


def test_sql_write_boundary():
    from deanery_api.agent_tools import PROPOSAL_TABLES, parse_change
    from deanery_api.errors import ApiError
    assert len(PROPOSAL_TABLES) == 10
    assert parse_change('UPDATE deanery.grade SET points=42,is_absent=false WHERE grade_id=1')['values'] == {'points': 42, 'is_absent': False}
    for sql in ('UPDATE deanery.grade SET student_id=2,points=42 WHERE grade_id=1',
                'INSERT INTO deanery.grade_correction(grade_id) VALUES(1)',
                'CALL deanery.sp_decide_grade_correction(1,true,NULL)'):
        try:
            parse_change(sql)
        except ApiError as error:
            assert error.code == 'query_rejected'
        else:
            raise AssertionError('Protected correction bypass accepted')


async def main():
    env = settings()
    assert env.get('RUN_AGENT_INTEGRATION_TESTS') == '1', 'Explicit isolated database/fixture lease required'
    path = output_directory(env) / ('agent-flow-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
    evidence = {'started_at': datetime.now(timezone.utc).isoformat(), 'provider': 'local deterministic protocol fixture', 'cases': []}

    def save():
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding='utf8')

    def record(name, request, expected, actual, checks):
        evidence['cases'].append({'id': name, 'request': request, 'expected': expected, 'actual': actual,
                                  'assertions': checks, 'status': 'pass' if all(checks.values()) else 'fail'})
        save()
        assert all(checks.values()), name + ': ' + ', '.join(k for k, v in checks.items() if not v)

    clients = {}
    with psycopg.connect(env['TEST_MIGRATION_DATABASE_URL'], autocommit=True, row_factory=dict_row) as db:
        def one(query, values=()):
            return db.execute(query, values).fetchone()

        def grade(grade_id):
            return one('SELECT * FROM deanery.grade WHERE grade_id=%s', (grade_id,))

        def counters():
            return one("SELECT (SELECT count(*) FROM public.sql_change_proposals) AS proposals,"
                       "(SELECT count(*) FROM deanery.grade_correction) AS corrections,"
                       "(SELECT coalesce(max(audit_id),0) FROM deanery.audit_log) AS audit")

        evidence['database'] = one('SELECT current_database() AS name,(SELECT version_num FROM public.alembic_version) AS head')
        evidence['source_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in (ROOT / 'sources/deanery_db').glob('*.sql')}
        evidence['owned_sha256'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in (ROOT / 'backend/deanery_api/agent_tools.py', ROOT / 'integrations/dean_agent_adapter/app.py')}
        try:
            for role, default in [('TEACHER', 'teacher'), ('STAFF', 'staff'), ('DIRECTOR', 'director')]:
                login = env.get('TEST_' + role + '_LOGIN', default)
                actor = one('SELECT u.user_id,u.login,r.code,e.employee_id FROM deanery.app_user u '
                            'JOIN deanery.app_role r USING(role_id) LEFT JOIN deanery.employee e USING(person_id) '
                            'WHERE u.login=%s AND u.is_active', (login,))
                assert actor, 'Configure a seeded active ' + role + ' account'
                client = httpx.AsyncClient(base_url=env.get('BASE_URL', 'http://127.0.0.1:8000'), timeout=90,
                                          trust_env=False, headers={'Origin': 'http://localhost:5173'})
                clients[role] = (client, actor)
                response = await client.post('/api/v1/auth/login', json={'login': login, 'password': env['TEST_' + role + '_PASSWORD']})
                assert response.status_code == 200, 'Fixture login failed for ' + role
                client.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
            teacher, initiator = clients['TEACHER']; staff, approver = clients['STAFF']; director, decider = clients['DIRECTOR']
            evidence['actors'] = {role: actor for role, (_, actor) in clients.items()}
            open_grade = one("SELECT g.* FROM deanery.grade g JOIN deanery.grade_sheet gs USING(sheet_id) "
                             "WHERE gs.status='open' AND gs.examiner_id=%s ORDER BY g.grade_id LIMIT 1", (initiator['employee_id'],))
            band = one('SELECT min(min_points) AS lo,max(max_points) AS hi FROM deanery.score_band')
            if not open_grade:
                # The source seed deliberately leaves retakes empty. Add one
                # independent ordinary sheet with every native guard enabled.
                admin = one("SELECT user_id FROM deanery.app_user WHERE login='admin'")['user_id']
                with db.transaction():
                    db.execute("SELECT backend.set_actor(%s,'integration-open-grade-fixture')", (admin,))
                    db.execute("SET LOCAL TIME ZONE 'Europe/Moscow'")
                    group = one('SELECT g.group_id,g.curriculum_id FROM deanery.study_group g '
                                'JOIN deanery.teaching_assignment a USING(group_id) WHERE a.teacher_id=%s ORDER BY g.group_id LIMIT 1', (initiator['employee_id'],))
                    department = one('SELECT department_id FROM deanery.teacher WHERE employee_id=%s', (initiator['employee_id'],))
                    discipline = one('INSERT INTO deanery.discipline(name,department_id) VALUES(%s,%s) RETURNING discipline_id',
                                     ('Integration ' + uuid4().hex, department['department_id']))
                    item = one('INSERT INTO deanery.curriculum_item(curriculum_id,discipline_id,semester,control_type_id,lecture_hours) '
                               'VALUES(%s,%s,1,(SELECT control_type_id FROM deanery.control_type WHERE is_graded ORDER BY control_type_id LIMIT 1),36) RETURNING item_id',
                               (group['curriculum_id'], discipline['discipline_id']))
                    db.execute("SELECT backend.command('create_grade_sheet')")
                    sheet = one('INSERT INTO deanery.grade_sheet(sheet_number,item_id,group_id,examiner_id,issue_date,exam_date) '
                                'VALUES(%s,%s,%s,%s,CURRENT_DATE,CURRENT_DATE) RETURNING *',
                                ('I' + uuid4().hex[:15], item['item_id'], group['group_id'], initiator['employee_id']))
                    student = one('SELECT s.student_id FROM deanery.student s JOIN deanery.student_status st USING(status_id) '
                                  'WHERE s.group_id=%s AND st.is_active ORDER BY s.student_id LIMIT 1', (group['group_id'],))
                    db.execute("SELECT backend.command('record_grade')")
                    open_grade = one('INSERT INTO deanery.grade(sheet_id,student_id,points) VALUES(%s,%s,%s) RETURNING *',
                                     (sheet['sheet_id'], student['student_id'], band['lo']))
                    db.execute("SELECT backend.set_actor(NULL,'')")
                evidence['open_grade_fixture'] = {'discipline': discipline, 'item': item, 'sheet': sheet, 'grade': open_grade}
            changed = band['lo'] if open_grade['points'] != band['lo'] else band['lo'] + 1

            async def tool(client, name, arguments, stream=False):
                message = 'VERIFY_TOOL:' + json.dumps({'name': name, 'arguments': arguments}, ensure_ascii=False)
                response = await client.post('/api/v1/agent/chat' + ('/stream' if stream else ''), json={'message': message})
                assert response.status_code == 200, response.text
                if stream:
                    frames = [dict(line.split(':', 1) for line in frame.splitlines() if ':' in line)
                              for frame in response.text.strip().split('\n\n')]
                    body = next(json.loads(f['data']) for f in frames if f.get('event', '').strip() == 'done')
                else:
                    body = response.json()
                return json.loads(json.loads(body['answer'])['tool_results'][0]), body

            async def propose(points, grade_id=None):
                sql = f"UPDATE deanery.grade SET points={points},is_absent=false WHERE grade_id={grade_id or open_grade['grade_id']}"
                return await tool(teacher, 'propose_sql_change', {'sql': sql,
                    'explanation': 'Проверка исправления оценки через исходного агента', 'reason': 'Исправление ошибочно перенесённых баллов'})

            proposal, chat = await propose(changed)
            record('OPEN-PREVIEW-NO-EFFECT', {'tool': 'propose_sql_change', 'grade_id': open_grade['grade_id'], 'points': changed},
                   {'proposal': 'pending', 'grade_unchanged': True}, {'tool': proposal, 'chat': chat, 'grade': grade(open_grade['grade_id'])},
                   {'proposal_created': 'proposal_id' in proposal, 'grade_unchanged': grade(open_grade['grade_id']) == open_grade})
            route = '/api/v1/agent/proposals/' + proposal['proposal_id']
            payload = {'approve': True, 'reason': 'Проверено уполномоченным сотрудником деканата'}
            denied = await teacher.post(route + '/decision', json=payload, headers={'Idempotency-Key': str(uuid4())})
            reviewed = await staff.get(route)
            record('DISTINCT-STAFF-REVIEW', {'proposal_id': proposal['proposal_id']}, {'teacher_decision': 403, 'staff_review': 200},
                   {'teacher': {'status': denied.status_code, 'body': denied.json()}, 'staff': {'status': reviewed.status_code, 'body': reviewed.json()}},
                   {'teacher_cannot_decide': denied.status_code == 403, 'staff_can_review': reviewed.status_code == 200})
            before = counters(); key = str(uuid4())
            results = await asyncio.gather(*(staff.post(route + '/decision', json=payload, headers={'Idempotency-Key': key}) for _ in range(2)))
            run = one('SELECT id,canonical_request_id FROM backend.agent_runs WHERE upstream_run_id=%s', (chat['run_id'],))
            audits = db.execute("SELECT * FROM deanery.audit_log WHERE audit_id>%s AND table_name='grade' ORDER BY audit_id", (before['audit'],)).fetchall()
            events = db.execute("SELECT * FROM backend.events WHERE action='proposal_decision' AND record_key->>'proposal_id'=%s", (proposal['proposal_id'],)).fetchall()
            record('DISTINCT-STAFF-CONCURRENT-APPROVAL', {'proposal_id': proposal['proposal_id'], 'decision': payload, 'same_key': True},
                   {'statuses': [200, 200], 'grade_audits': 1, 'decision_events': 1, 'actor': approver['user_id'], 'canonical': run['canonical_request_id']},
                   {'responses': [{'status': r.status_code, 'body': r.json()} for r in results], 'grade': grade(open_grade['grade_id']), 'audits': audits, 'events': events},
                   {'both_200': all(r.status_code == 200 for r in results), 'same_result': results[0].json() == results[1].json(),
                    'grade_applied': grade(open_grade['grade_id'])['points'] == changed, 'one_audit': len(audits) == 1,
                    'one_event': len(events) == 1, 'actor_and_canonical': bool(audits) and all(a['app_user_id'] == approver['user_id'] and a['agent_request_id'] == run['canonical_request_id'] and a['agent_run_id'] == run['id'] for a in audits)})
            foreign_session = await staff.post('/api/v1/agent/chat', json={'message': 'История', 'session_id': chat['session_id']})
            record('CHAT-STILL-OWNER-ONLY', {'session_id': chat['session_id'], 'actor': approver['user_id']}, {'status': 404},
                   {'status': foreign_session.status_code, 'body': foreign_session.json()}, {'denied': foreign_session.status_code == 404})
            next_points = changed + 1
            stale, _ = await propose(next_points)
            assert 'proposal_id' in stale, stale
            human = await staff.post('/api/v1/workflows/record_grade', json={
                'sheet_id': open_grade['sheet_id'], 'student_id': open_grade['student_id'],
                'points': next_points + 1, 'is_absent': False}, headers={'Idempotency-Key': str(uuid4())})
            assert human.status_code == 200, human.text
            current = grade(open_grade['grade_id']); before = counters()
            stale_route = '/api/v1/agent/proposals/' + stale['proposal_id'] + '/decision'
            rejected_stale = await staff.post(stale_route, json=payload, headers={'Idempotency-Key': str(uuid4())})
            rejected = await staff.post(stale_route, json={'approve': False, 'reason': 'Изменение больше не требуется после ручного уточнения'},
                                        headers={'Idempotency-Key': str(uuid4())})
            after = counters()
            events = db.execute("SELECT * FROM backend.events WHERE action='proposal_decision' AND record_key->>'proposal_id'=%s", (stale['proposal_id'],)).fetchall()
            record('STALE-APPROVAL-AND-REJECT-NO-EFFECT', {'proposal': stale['proposal_id'], 'human_change': human.json()},
                   {'stale': 'stale_proposal', 'reject': 200, 'grade_unchanged': True, 'decision_events': 1},
                   {'stale': {'status': rejected_stale.status_code, 'body': rejected_stale.json()}, 'rejected': {'status': rejected.status_code, 'body': rejected.json()},
                    'before': before, 'after': after, 'grade': grade(open_grade['grade_id']), 'events': events},
                   {'exact_stale_error': rejected_stale.status_code == 409 and rejected_stale.json().get('error', {}).get('code') == 'stale_proposal',
                    'rejected': rejected.status_code == 200 and rejected.json()['status'] == 'rejected',
                    'grade_unchanged': grade(open_grade['grade_id']) == current, 'no_domain_audit': after['audit'] == before['audit'], 'one_event': len(events) == 1})
            revoked, _ = await propose(next_points)
            assert 'proposal_id' in revoked, revoked
            admin = one("SELECT user_id FROM deanery.app_user WHERE login='admin'")['user_id']
            db.execute("SELECT backend.set_actor(%s,'test-exact-initiator-revocation')", (admin,))
            db.execute('UPDATE deanery.app_user SET is_active=false WHERE user_id=%s', (initiator['user_id'],))
            try:
                current = grade(open_grade['grade_id']); before = counters()
                blocked = await staff.post('/api/v1/agent/proposals/' + revoked['proposal_id'] + '/decision', json=payload, headers={'Idempotency-Key': str(uuid4())})
                after = counters()
                stored = one('SELECT status FROM public.sql_change_proposals WHERE id=%s', (revoked['proposal_id'],))
                record('REVOKED-INITIATOR-NO-EFFECT', {'initiator_id': initiator['user_id'], 'proposal_id': revoked['proposal_id']},
                       {'status': 404, 'proposal': 'pending', 'grade_unchanged': True}, {'http': blocked.status_code, 'body': blocked.json(),
                       'proposal': stored, 'before': before, 'after': after, 'grade': grade(open_grade['grade_id'])},
                       {'denied': blocked.status_code == 404, 'pending': stored['status'] == 'pending',
                        'grade_unchanged': grade(open_grade['grade_id']) == current, 'no_domain_audit': before == after})
            finally:
                db.execute('UPDATE deanery.app_user SET is_active=true WHERE user_id=%s', (initiator['user_id'],))
                db.execute("SELECT backend.set_actor(NULL,'')")
            staff_scope = one('SELECT institute_id FROM deanery.dean_office_staff WHERE employee_id=%s', (approver['employee_id'],))
            other_institute = one('SELECT institute_id FROM deanery.institute WHERE institute_id<>%s ORDER BY institute_id LIMIT 1', (staff_scope['institute_id'],))
            assert other_institute, 'The source seed must have another institute'
            db.execute("SELECT backend.set_actor(%s,'test-exact-reviewer-scope')", (admin,))
            db.execute('UPDATE deanery.dean_office_staff SET institute_id=%s WHERE employee_id=%s', (other_institute['institute_id'], approver['employee_id']))
            try:
                before_counts = counters(); current = grade(open_grade['grade_id'])
                foreign_route = '/api/v1/agent/proposals/' + revoked['proposal_id']
                foreign_read = await staff.get(foreign_route)
                foreign_decision = await staff.post(foreign_route + '/decision', json=payload, headers={'Idempotency-Key': str(uuid4())})
                record('CURRENT-FOREIGN-STAFF-CANNOT-REVIEW', {'proposal_id': revoked['proposal_id'], 'current_reviewer_institute': other_institute['institute_id'], 'original_institute': staff_scope['institute_id']},
                       {'read': 404, 'decision': 404, 'no_effect': True},
                       {'read': {'status': foreign_read.status_code, 'body': foreign_read.json()}, 'decision': {'status': foreign_decision.status_code, 'body': foreign_decision.json()}},
                       {'both_denied': foreign_read.status_code == foreign_decision.status_code == 404,
                        'grade_unchanged': grade(open_grade['grade_id']) == current, 'no_audit': counters() == before_counts})
            finally:
                db.execute('UPDATE deanery.dean_office_staff SET institute_id=%s WHERE employee_id=%s', (staff_scope['institute_id'], approver['employee_id']))
                db.execute("SELECT backend.set_actor(NULL,'')")
            closed = one("SELECT g.* FROM deanery.grade g JOIN deanery.grade_sheet gs USING(sheet_id) WHERE gs.status='closed' "
                         "AND gs.examiner_id=%s AND NOT EXISTS(SELECT 1 FROM deanery.grade_correction c WHERE c.grade_id=g.grade_id AND c.status='pending') "
                         "AND NOT EXISTS(SELECT 1 FROM deanery.grade g2 JOIN deanery.grade_sheet s2 USING(sheet_id) WHERE g2.student_id=g.student_id "
                         "AND s2.item_id=gs.item_id AND s2.stage=gs.stage AND s2.status<>'cancelled' "
                         "AND (coalesce(s2.exam_date,'infinity'::date),s2.sheet_id)>(gs.exam_date,gs.sheet_id)) ORDER BY g.grade_id LIMIT 1", (initiator['employee_id'],))
            assert closed, 'Seed fixture needs a correctable closed grade of the configured teacher'
            new_points = band['lo'] if closed['points'] != band['lo'] else band['lo'] + 1
            before = counters(); correction, correction_chat = await propose(new_points, closed['grade_id']); after = counters()
            saved = one('SELECT * FROM deanery.grade_correction WHERE correction_id=%s', (correction.get('correction_id'),))
            record('CLOSED-GRADE-NATIVE-PENDING', {'grade_id': closed['grade_id'], 'points': new_points},
                   {'status': 'pending', 'generic_proposals_added': 0, 'native_requests_added': 1, 'grade_unchanged': True},
                   {'tool': correction, 'chat': correction_chat, 'before': before, 'after': after, 'correction': saved, 'grade': grade(closed['grade_id'])},
                   {'typed_pending': set(correction) == {'correction_id', 'status'} and correction['status'] == 'pending',
                    'no_generic_proposal': after['proposals'] == before['proposals'], 'one_native_request': after['corrections'] == before['corrections'] + 1,
                    'grade_unchanged': grade(closed['grade_id']) == closed, 'exact_requester': bool(saved) and saved['requested_by_id'] == initiator['user_id']})
            endpoint = '/api/v1/workflows/decide_grade_correction'
            decision = {'correction_id': correction['correction_id'], 'approve': True, 'comment': 'Подтверждено руководителем института'}
            unauthorized = await staff.post(endpoint, json=decision, headers={'Idempotency-Key': str(uuid4())})
            before = counters(); applied = await director.post(endpoint, json=decision, headers={'Idempotency-Key': str(uuid4())})
            run = one('SELECT id,canonical_request_id FROM backend.agent_runs WHERE upstream_run_id=%s', (correction_chat['run_id'],))
            audits = db.execute("SELECT * FROM deanery.audit_log WHERE audit_id>%s AND table_name='grade' ORDER BY audit_id", (before['audit'],)).fetchall()
            record('CLOSED-GRADE-HUMAN-DECISION', decision, {'staff_denied': True, 'director': 200, 'status': 'applied', 'actor': decider['user_id'], 'canonical': run['canonical_request_id']},
                   {'staff': {'status': unauthorized.status_code, 'body': unauthorized.json()}, 'director': {'status': applied.status_code, 'body': applied.json()}, 'grade': grade(closed['grade_id']), 'audit': audits},
                   {'staff_denied': unauthorized.status_code in (403, 409), 'director_applied': applied.status_code == 200 and applied.json().get('status') == 'applied',
                    'grade_applied': grade(closed['grade_id'])['points'] == new_points, 'one_audit': len(audits) == 1,
                    'actual_decider_original_canonical': bool(audits) and all(a['app_user_id'] == decider['user_id'] and a['agent_request_id'] == run['canonical_request_id'] for a in audits)})
            query = 'SELECT grade_id,points FROM deanery.grade ORDER BY grade_id LIMIT 2'
            value, streamed = await tool(teacher, 'query_deanery', {'sql': query}, stream=True)
            record('ORIGINAL-SSE-SIGNED-TOOLS', {'sql': query, 'stream': True}, {'returned': 2}, {'tool': value, 'chat': streamed},
                   {'scoped_sql_succeeds': value.get('returned') == 2, 'original_tool': streamed['tools_used'] == ['query_deanery']})
            for table in ('audit_log', 'audit_log_detail'):
                result, _ = await tool(director, 'query_deanery', {'sql': f'SELECT audit_id FROM deanery.{table} LIMIT 1'})
                record('RAW-JOURNAL-DENIED-' + table, {'table': table}, {'error': 'query_rejected'}, result,
                       {'denied': result.get('error', '').startswith('query_rejected:')})
            from deanery_api.agent_tools import parse_change, preview_change
            from deanery_api.auth import load_principal
            from deanery_api.errors import ApiError
            async with await psycopg.AsyncConnection.connect(env['TEST_DATABASE_URL'], autocommit=True, row_factory=dict_row) as runtime:
                await runtime.execute('SELECT backend.set_actor(%s,%s)', (initiator['user_id'], 'preserved-review-context'))
                await runtime.execute('SELECT backend.set_audit_run(%s)', (correction_chat['run_id'],))
                await runtime.execute("SELECT backend.command('preserved-command')")
                pid = runtime.info.backend_pid
                before_context = one('SELECT * FROM backend.actor_context WHERE pid=%s', (pid,))
                before_grade = grade(open_grade['grade_id']); before_counts = counters()
                reviewer = await load_principal(runtime, approver['user_id'])
                context_results = []
                try:
                    for points, should_fail in ((band['lo'], False), (-1, True)):
                        failure = None
                        try:
                            async with runtime.transaction():
                                await runtime.execute('SET LOCAL search_path=deanery,backend,public')
                                await preview_change(runtime, reviewer, parse_change(f"UPDATE deanery.grade SET points={points} WHERE grade_id={open_grade['grade_id']}"), uuid4())
                        except (ApiError, psycopg.Error) as error:
                            failure = {'type': type(error).__name__, 'code': getattr(error, 'code', None), 'sqlstate': getattr(error, 'sqlstate', None)}
                        context_results.append({'invalid_points': should_fail, 'failure': failure,
                            'context_after': one('SELECT * FROM backend.actor_context WHERE pid=%s', (pid,))})
                    record('PREVIEW-RESTORES-COMPLETE-PROTECTED-CONTEXT', {'actor': initiator['user_id'], 'reviewer': approver['user_id'], 'success_and_exception': True},
                           {'context_unchanged': True, 'grade_unchanged': True, 'audit_unchanged': True},
                           {'before_context': before_context, 'results': context_results, 'before_grade': before_grade, 'after_grade': grade(open_grade['grade_id'])},
                           {'both_contexts_restored': all(r['context_after'] == before_context for r in context_results),
                            'correct_success_and_exception': context_results[0]['failure'] is None and context_results[1]['failure'] is not None,
                            'grade_unchanged': grade(open_grade['grade_id']) == before_grade, 'no_audit': counters() == before_counts})
                finally:
                    await runtime.execute("SELECT backend.set_actor(NULL,'')")
                assert one('SELECT 1 FROM backend.actor_context WHERE pid=%s', (pid,)) is None
            denied_helpers = []
            for role in ('deanery_reader', 'deanery_jobs', 'deanery_upstream'):
                for statement, argument in [('SELECT backend.set_proposal_audit(%s)', revoked['proposal_id']),
                                             ('SELECT backend.set_correction_audit(%s)', correction['correction_id'])]:
                    try:
                        with db.transaction(force_rollback=True):
                            db.execute('SET LOCAL ROLE ' + role)
                            db.execute(statement, (argument,))
                    except psycopg.Error as error:
                        denied_helpers.append({'role': role, 'statement': statement, 'sqlstate': error.sqlstate})
                    else:
                        denied_helpers.append({'role': role, 'statement': statement, 'sqlstate': None})
            record('NATIVE-HELPER-PRIVILEGE-BOUNDARY', {'helpers': ['set_proposal_audit(uuid)', 'set_correction_audit(integer)']},
                   {'reader_jobs_upstream': '42501'}, denied_helpers, {'all_six_denied': len(denied_helpers) == 6 and all(r['sqlstate'] == '42501' for r in denied_helpers)})
            pending, pending_chat = await propose(new_points + 1, closed['grade_id'])
            assert pending.get('status') == 'pending', pending
            before_correction = one('SELECT * FROM deanery.grade_correction WHERE correction_id=%s', (pending['correction_id'],))
            before_counts = counters(); failure = None
            try:
                with db.transaction(force_rollback=True):
                    db.execute('UPDATE public.chat_sessions SET actor_id=%s WHERE id=%s', ('user:' + str(approver['user_id']), pending_chat['session_id']))
                    db.execute('SET LOCAL ROLE deanery_runtime')
                    db.execute("SELECT backend.set_actor(%s,'broken-origin-test')", (decider['user_id'],))
                    db.execute('SELECT backend.decide_grade_correction(%s,true,%s)', (pending['correction_id'], 'Invalid stored chain must fail closed'))
            except psycopg.Error as error:
                failure = {'sqlstate': error.sqlstate, 'message': error.diag.message_primary}
            record('BROKEN-NONNULL-CORRECTION-ORIGIN-FAILS-CLOSED', {'correction_id': pending['correction_id'], 'changed_only_in_rollback': 'stored chat owner'},
                   {'sqlstate': '42501', 'correction_unchanged': True, 'no_audit': True}, {'error': failure, 'correction': one('SELECT * FROM deanery.grade_correction WHERE correction_id=%s', (pending['correction_id'],))},
                   {'exact_denial': bool(failure) and failure['sqlstate'] == '42501',
                    'unchanged': one('SELECT * FROM deanery.grade_correction WHERE correction_id=%s', (pending['correction_id'],)) == before_correction,
                    'no_audit': counters() == before_counts})
        except BaseException as error:
            evidence['failure'] = {'type': type(error).__name__, 'message': str(error)[:1500]}
            raise
        finally:
            for client, _ in clients.values():
                await client.aclose()
            save()
            print(json.dumps({'cases': len(evidence['cases']), 'passed': sum(row['status'] == 'pass' for row in evidence['cases']), 'evidence': str(path)}))


if __name__ == '__main__':
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None)
