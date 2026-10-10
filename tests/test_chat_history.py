"""Focused history contracts plus opt-in HTTP/PostgreSQL checks on synthetic data."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
import pytest
from pydantic import ValidationError

from deanery_api import agent_gateway as gateway
from deanery_api.auth import hasher
from deanery_api.errors import ApiError
from test_integration_support import settings


def test_titles_and_blank_prompts_are_rejected_before_admission():
    assert gateway.RenameSession(title='  Учебный план  ').title == 'Учебный план'
    for value in ('', '  ', 'x'*161):
        with pytest.raises(ValidationError):
            gateway.RenameSession(title=value)
    with pytest.raises(ApiError) as error:
        gateway.ChatRequest(message=' \n\t')
    assert error.value.code == 'message_required'
    with pytest.raises(ValidationError):
        gateway.RenameSession(title='Name', actor_id='user:2')


def test_completion_metadata_is_bounded_and_allowlisted():
    assert gateway.Completion(status='done', answer='OK').tools_used is None
    assert gateway.Completion(status='done', answer='OK', tools_used=['query_deanery']).tools_used == ['query_deanery']
    for value in (['arbitrary_tool'], ['query_deanery']*301):
        with pytest.raises(ValidationError):
            gateway.Completion(status='done', answer='OK', tools_used=value)


async def test_cancelled_json_response_marks_outcome_unknown(monkeypatch):
    run_id, session_id = uuid4(), uuid4()

    class InterruptedResponse:
        async def aiter_bytes(self):
            raise asyncio.CancelledError()
            yield b''

        async def aclose(self):
            pass

    monkeypatch.setattr(gateway, 'get_settings', lambda: SimpleNamespace(agent_timeout_seconds=10))
    monkeypatch.setattr(gateway, 'upstream_request', AsyncMock(return_value=(run_id, session_id, InterruptedResponse(), None)))
    finish = AsyncMock()
    monkeypatch.setattr(gateway, 'finish', finish)
    with pytest.raises(asyncio.CancelledError):
        await gateway.chat(gateway.ChatRequest(message='Question'), None, None)
    finish.assert_awaited_once_with(run_id, 'ambiguous')


async def test_stream_completion_preserves_tools_and_error_preserves_ids(monkeypatch):
    run_id, session_id = uuid4(), uuid4()
    monkeypatch.setattr(gateway, 'get_settings', lambda: SimpleNamespace(agent_timeout_seconds=10))
    for event in ('done', 'error'):
        done = {'run_id': str(uuid4()), 'session_id': str(session_id), 'answer': 'Saved', 'proposals': [], 'tools_used': ['query_deanery']}
        raw = 'event: '+event+'\ndata: '+json.dumps(done if event == 'done' else {'message': 'private upstream error'})+'\n\n'
        response = httpx.Response(200, content=raw.encode(), headers={'Content-Type': 'text/event-stream'})
        monkeypatch.setattr(gateway, 'upstream_request', AsyncMock(return_value=(run_id, session_id, response, None)))
        finish = AsyncMock()
        monkeypatch.setattr(gateway, 'finish', finish)
        stream = await gateway.chat_stream(gateway.ChatRequest(message='Question'), None, None)
        result = ''.join([frame async for frame in stream.body_iterator])
        if event == 'done':
            finish.assert_awaited_once_with(run_id, 'done', 'Saved', ['query_deanery'])
        else:
            assert str(run_id) in result and str(session_id) in result and 'private upstream error' not in result
            finish.assert_awaited_once_with(run_id, 'failed')


@pytest.mark.parametrize('event', ['done', 'error'])
async def test_stream_cancelled_during_outcome_write_preserves_ambiguous_run(monkeypatch, event):
    run_id, session_id = uuid4(), uuid4()
    done = {'run_id': str(uuid4()), 'session_id': str(session_id), 'answer': 'Saved', 'proposals': [], 'tools_used': []}
    raw = 'event: '+event+'\ndata: '+json.dumps(done if event == 'done' else {'message': 'Failed'})+'\n\n'
    response = httpx.Response(200, content=raw.encode(), headers={'Content-Type': 'text/event-stream'})
    monkeypatch.setattr(gateway, 'get_settings', lambda: SimpleNamespace(agent_timeout_seconds=10))
    monkeypatch.setattr(gateway, 'upstream_request', AsyncMock(return_value=(run_id, session_id, response, None)))
    finish = AsyncMock(side_effect=[asyncio.CancelledError(), None])
    monkeypatch.setattr(gateway, 'finish', finish)
    stream = await gateway.chat_stream(gateway.ChatRequest(message='Question'), None, None)
    with pytest.raises(asyncio.CancelledError):
        _ = [frame async for frame in stream.body_iterator]
    assert finish.await_count == 2
    finish.assert_awaited_with(run_id, 'ambiguous')


@pytest.fixture(scope='module')
def history_api():
    env = settings()
    if env.get('RUN_CHAT_HISTORY_TESTS') != '1':
        pytest.skip('Set RUN_CHAT_HISTORY_TESTS=1 with an isolated synthetic PostgreSQL/API lease')
    assert env.get('TEST_DATABASE_URL') and env.get('BASE_URL')
    db = psycopg.connect(env['TEST_DATABASE_URL'], row_factory=dict_row, autocommit=True)
    assert db.execute('SELECT version_num FROM alembic_version').fetchone()['version_num'] == '0006'
    client = httpx.Client(base_url=env['BASE_URL'], trust_env=False, timeout=35)
    users = []
    password = uuid4().hex+'aA7!'
    for _ in range(2):
        login = 'chat_test_'+uuid4().hex[:16]
        person = db.execute("INSERT INTO deanery.person(last_name,first_name,gender,birth_date) VALUES('History','Verification','M','2000-01-01') RETURNING person_id").fetchone()
        row = db.execute("INSERT INTO deanery.app_user(person_id,login,password_hash,role_id) SELECT %s,%s,%s,role_id FROM deanery.app_role WHERE code='admin' RETURNING user_id",
                         (person['person_id'], login, hasher.hash(password))).fetchone()
        body = {'login': login, 'password': password}
        response = client.post('/api/v1/auth/login', headers={'Origin': 'http://localhost:5173'}, json=body)
        assert response.status_code == 200
        users.append({'id': row['user_id'], 'credentials': body, 'headers': {'Authorization': 'Bearer '+response.json()['access_token']}})
    yield SimpleNamespace(db=db, client=client, users=users, env=env)
    client.close()
    db.close()


def make_session(api, owner=0, legacy=False):
    session_id = uuid4()
    created = datetime.now(timezone.utc)-timedelta(days=3)
    api.db.execute('INSERT INTO public.chat_sessions(id,actor_id,created_at) VALUES(%s,%s,%s)',
                   (session_id, f"user:{api.users[owner]['id']}", created))
    if legacy:
        api.db.execute("INSERT INTO public.chat_messages(session_id,role,content,created_at) VALUES(%s,'user','Legacy question',%s),(%s,'assistant','Legacy answer',%s)",
                       (session_id, created, session_id, created))
    return session_id


def make_run(api, session_id, message='Question', status='done', answer='Answer', upstream_copy=True, tools=None):
    run_id = uuid4()
    api.db.execute('SELECT backend.set_actor(%s,%s)', (api.users[0]['id'], str(run_id)))
    api.db.execute('SELECT backend.start_agent_run(%s,%s,%s,%s)',
                   (run_id, session_id, datetime.now(timezone.utc)+timedelta(hours=1), message))
    if status == 'done' and upstream_copy:
        api.db.execute("INSERT INTO public.chat_messages(session_id,role,content) VALUES(%s,'user',%s),(%s,'assistant',%s)",
                       (session_id, message, session_id, answer))
    if status != 'running':
        api.db.execute('SELECT backend.finish_agent_run(%s,%s,%s,%s)',
                       (run_id, api.users[0]['id'], status, answer if status == 'done' else None))
    if tools is not None:
        api.db.execute('UPDATE backend.agent_runs SET tools_used=%s WHERE id=%s', (list(tools), run_id))
    return run_id


def get_history(api, session_id, **params):
    response = api.client.get('/api/v1/agent/sessions/'+str(session_id), params=params, headers=api.users[0]['headers'])
    assert response.status_code == 200, response.text
    return response.json()


def test_live_legacy_and_managed_history_order_paging_no_duplicates(history_api):
    api = history_api
    sid = make_session(api, legacy=True)
    first = make_run(api, sid, message='Repeated question', answer='Repeated answer', tools=['query_deanery'])
    make_run(api, sid, message='Repeated question', answer='Repeated answer')
    collected = []
    offset = 0
    while offset is not None:
        page = get_history(api, sid, limit=1, offset=offset)
        collected.extend(page['messages'])
        offset = page['next_offset']
    assert [m['content'] for m in collected] == ['Legacy question', 'Legacy answer', 'Repeated question', 'Repeated answer', 'Repeated question', 'Repeated answer']
    assert len({m['id'] for m in collected}) == 6
    assert collected[3]['tools_used'] == ['query_deanery'] and collected[3]['run_id'] == str(first)
    assert all(m['status'] == 'done' for m in collected[2:])
    # New authenticated HTTP clients reconstruct the same history without browser state.
    with httpx.Client(base_url=api.env['BASE_URL'], trust_env=False) as fresh:
        login = fresh.post('/api/v1/auth/login', json=api.users[0]['credentials'], headers={'Origin': 'http://localhost:5173'})
        result = fresh.get('/api/v1/agent/sessions/'+str(sid), headers={'Authorization': 'Bearer '+login.json()['access_token']})
        assert result.status_code == 200 and result.json()['messages'] == collected


def test_live_owner_isolation_rename_delete_and_validation(history_api):
    api = history_api
    sid = make_session(api)
    make_run(api, sid)
    path = '/api/v1/agent/sessions/'+str(sid)
    for headers, expected in (({}, 401), (api.users[1]['headers'], 404)):
        assert api.client.get(path, headers=headers).status_code == expected
        assert api.client.patch(path, json={'title': 'Foreign'}, headers=headers).status_code == expected
        assert api.client.delete(path, headers=headers).status_code == expected
    other_list = api.client.get('/api/v1/agent/sessions', headers=api.users[1]['headers']).json()
    assert str(sid) not in {item['id'] for item in other_list['items']}
    for title in ('', '   ', 'x'*161):
        assert api.client.patch(path, json={'title': title}, headers=api.users[0]['headers']).status_code == 422
    renamed = api.client.patch(path, json={'title': '  Сохранённый чат  '}, headers=api.users[0]['headers'])
    assert renamed.status_code == 200 and renamed.json()['title'] == 'Сохранённый чат'
    assert get_history(api, sid)['title'] == 'Сохранённый чат'
    assert api.client.get(path+'?offset=-1', headers=api.users[0]['headers']).status_code == 422
    assert api.client.get(path+'?limit=201', headers=api.users[0]['headers']).status_code == 422
    assert api.client.delete(path, headers=api.users[0]['headers']).status_code == 204
    assert api.client.get(path, headers=api.users[0]['headers']).status_code == 404
    assert api.client.patch(path, json={'title': 'Restore'}, headers=api.users[0]['headers']).status_code == 404
    assert api.client.post('/api/v1/agent/chat', json={'message': 'Again', 'session_id': str(sid)}, headers=api.users[0]['headers']).status_code == 404
    assert api.db.execute('SELECT deleted_at FROM public.chat_sessions WHERE id=%s', (sid,)).fetchone()['deleted_at'] is not None
    assert api.db.execute('SELECT count(*) n FROM backend.agent_runs WHERE session_id=%s', (sid,)).fetchone()['n'] == 1


@pytest.mark.parametrize('status', ['running', 'ambiguous', 'failed'])
def test_live_unfinished_and_failed_prompts_remain_visible(history_api, status):
    api = history_api
    sid = make_session(api)
    run = make_run(api, sid, status=status, upstream_copy=False)
    history = get_history(api, sid)
    assert history['busy'] is (status in ('running', 'ambiguous'))
    assert history['messages'][0]['content'] == 'Question' and history['messages'][0]['status'] == status
    assert len(history['messages']) == (1 if status == 'running' else 2)
    if status != 'running':
        assert history['messages'][1]['role'] == 'error'
    response = api.client.delete('/api/v1/agent/sessions/'+str(sid), headers=api.users[0]['headers'])
    assert response.status_code == (409 if status in ('running', 'ambiguous') else 204)
    if status in ('running', 'ambiguous'):
        api.db.execute('SELECT backend.finish_agent_run(%s,%s,%s,%s)', (run, api.users[0]['id'], 'done', 'Recovered final answer'))
        # A delayed ambiguous/failure or repeated callback must not overwrite a terminal result.
        assert not api.db.execute('SELECT backend.finish_agent_run(%s,%s,%s,NULL) AS changed', (run, api.users[0]['id'], 'ambiguous')).fetchone()['changed']
        assert not api.db.execute('SELECT backend.finish_agent_run(%s,%s,%s,%s) AS changed', (run, api.users[0]['id'], 'done', 'Duplicate')).fetchone()['changed']
        final = get_history(api, sid)
        assert not final['busy'] and final['messages'][1]['content'] == 'Recovered final answer'


def test_live_attachment_history_respects_revoked_file_access(history_api):
    api = history_api
    sid = make_session(api)
    run = make_run(api, sid)
    file_id, version_id = uuid4(), uuid4()
    api.db.execute("INSERT INTO backend.files(id,owner_id,title,purpose) VALUES(%s,%s,'Visible attachment','attachment')", (file_id, api.users[0]['id']))
    api.db.execute("""INSERT INTO backend.file_versions(id,file_id,version,object_key,filename,mime,byte_size,sha256,state)
      VALUES(%s,%s,1,%s,'original.txt','text/plain',12,%s,'ready')""", (version_id, file_id, 'test/'+str(version_id), 'a'*64))
    api.db.execute('INSERT INTO backend.agent_run_attachments(run_id,file_id,version_id,ordinal) VALUES(%s,%s,%s,0)', (run, file_id, version_id))
    before = get_history(api, sid)['messages'][0]
    assert before['attachments'][0]['version_id'] == str(version_id) and before['attachments'][0]['byte_size'] == 12
    api.db.execute("UPDATE backend.file_versions SET state='rejected' WHERE id=%s", (version_id,))
    after = get_history(api, sid)['messages'][0]
    assert after['attachments'] == [] and after['unavailable_attachment_count'] == 1


def test_live_runtime_rls_does_not_reveal_foreign_chat_text(history_api):
    api = history_api
    sid = make_session(api, legacy=True)
    runtime_url = api.env.get('DATABASE_URL')
    assert runtime_url
    with psycopg.connect(runtime_url, row_factory=dict_row) as runtime:
        runtime.execute('SELECT backend.set_actor(%s,%s)', (api.users[1]['id'], str(uuid4())))
        assert runtime.execute('SELECT id FROM public.chat_sessions WHERE id=%s', (sid,)).fetchall() == []
        assert runtime.execute('SELECT content FROM public.chat_messages WHERE session_id=%s', (sid,)).fetchall() == []


def test_document_request_contract_only_advertises_student_creation():
    from deanery_api.resources import manifest
    roles = manifest['resources']['document_request']['operation_roles']
    assert 'student' in roles['create']
    assert roles['update'] == ['admin', 'director', 'dean_staff']


def test_live_completion_metadata_is_idempotent_and_legacy_callback_compatible(history_api, monkeypatch):
    from psycopg_pool import AsyncConnectionPool
    api = history_api
    sid = make_session(api)
    run = make_run(api, sid, status='running', upstream_copy=False)

    async def check():
        async with AsyncConnectionPool(api.env['DATABASE_URL'], open=False, kwargs={'row_factory': dict_row}) as pool:
            monkeypatch.setattr(gateway, 'get_pool', lambda: pool)
            await gateway.finish(run, 'done', 'First answer', ['query_deanery'])
            await gateway.finish(run, 'done', 'First answer', ['search_regulations'])
            await gateway.finish(run, 'failed')
            stored = api.db.execute('SELECT tools_used FROM backend.agent_runs WHERE id=%s', (run,)).fetchone()
            assert stored['tools_used'] == ['query_deanery']
            second = make_run(api, sid, status='running', upstream_copy=False)
            await gateway.finish(second, 'done', 'Older adapter')
            assert api.db.execute('SELECT tools_used FROM backend.agent_runs WHERE id=%s', (second,)).fetchone()['tools_used'] is None
            await gateway.finish(second, 'done', 'Older adapter', [])
            await gateway.finish(second, 'done', 'Older adapter', ['query_deanery'])
            assert api.db.execute('SELECT tools_used FROM backend.agent_runs WHERE id=%s', (second,)).fetchone()['tools_used'] == []

    asyncio.run(check(), loop_factory=asyncio.SelectorEventLoop)


def test_live_proposal_decision_and_revoked_scope_are_reloaded(history_api):
    api = history_api
    if api.env.get('RUN_AGENT_INTEGRATION_TESTS') != '1':
        pytest.skip('Requires the explicitly leased deterministic agent HTTP fixture')
    organization = api.db.execute('SELECT organization_id FROM deanery.organization ORDER BY organization_id LIMIT 1').fetchone()['organization_id']
    message = 'VERIFY_TOOL:'+json.dumps({'name': 'propose_sql_change', 'arguments': {
        'sql': f"INSERT INTO deanery.organization_contact(organization_id,last_name,first_name,job_title) VALUES({organization},'History','Review','Verification')",
        'explanation': 'Create a synthetic contact for a history review check',
        'reason': 'Explicit synthetic integration verification',
    }})
    chat = api.client.post('/api/v1/agent/chat', json={'message': message}, headers=api.users[0]['headers'])
    assert chat.status_code == 200, chat.text
    body = chat.json()
    assert len(body['proposals']) == 1, body
    proposal_id = body['proposals'][0]['id']
    before = get_history(api, body['session_id'])['messages'][-1]
    assert before['proposals'][0]['status'] == 'pending' and before['tools_used'] == ['propose_sql_change']
    rejected = api.client.post('/api/v1/agent/proposals/'+proposal_id+'/decision',
        json={'approve': False, 'reason': 'Reject this synthetic proposal without changing domain records'},
        headers={**api.users[1]['headers'], 'Idempotency-Key': str(uuid4())})
    assert rejected.status_code == 200, rejected.text
    assert get_history(api, body['session_id'])['messages'][-1]['proposals'][0]['status'] == 'rejected'
    old_role = api.db.execute('SELECT role_id FROM deanery.app_user WHERE user_id=%s', (api.users[0]['id'],)).fetchone()['role_id']
    try:
        api.db.execute("UPDATE deanery.app_user SET role_id=(SELECT role_id FROM deanery.app_role WHERE code='teacher') WHERE user_id=%s", (api.users[0]['id'],))
        hidden = get_history(api, body['session_id'])['messages'][-1]
        assert hidden['proposals'] == [] and hidden['unavailable_proposal_count'] == 1
    finally:
        api.db.execute('UPDATE deanery.app_user SET role_id=%s WHERE user_id=%s', (old_role, api.users[0]['id']))
