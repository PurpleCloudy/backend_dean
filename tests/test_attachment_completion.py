"""New tool provenance must cross validation, callback, SSE and PostgreSQL storage."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from psycopg.errors import CheckViolation
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError
import pytest

from deanery_api import agent_gateway as gateway
from integrations.dean_agent_adapter.security import sign
from test_chat_history import history_api, make_session, make_run, get_history

TOOLS = ['query_deanery', 'propose_sql_change', 'search_regulations', 'read_attachment_text']


def test_completion_accepts_reader_but_does_not_expand_sql_tool_endpoint():
    assert gateway.Completion(status='done', answer='Read', tools_used=TOOLS).tools_used == TOOLS
    with pytest.raises(ValidationError):
        gateway.Completion(status='done', answer='Read', tools_used=['unknown'])
    with pytest.raises(ValidationError):
        gateway.ToolCall(name='read_attachment_text', arguments={})


async def test_sse_database_failure_yields_safe_error_and_preserves_unknown_outcome(monkeypatch):
    run, session = uuid4(), uuid4()
    done = {'run_id': str(uuid4()), 'session_id': str(session), 'answer': 'Read', 'proposals': [], 'tools_used': ['read_attachment_text']}
    response = httpx.Response(200, content=('event: done\ndata: '+json.dumps(done)+'\n\n').encode(),
                              headers={'Content-Type': 'text/event-stream'})
    monkeypatch.setattr(gateway, 'get_settings', lambda: SimpleNamespace(agent_timeout_seconds=10))
    monkeypatch.setattr(gateway, 'upstream_request', AsyncMock(return_value=(run, session, response, None)))
    finish = AsyncMock(side_effect=[CheckViolation('private database detail'), None])
    monkeypatch.setattr(gateway, 'finish', finish)
    stream = await gateway.chat_stream(gateway.ChatRequest(message='Read'), None, None)
    frames = ''.join([frame async for frame in stream.body_iterator])
    assert 'event: error' in frames and 'event: done' not in frames
    assert str(run) in frames and str(session) in frames and 'private database detail' not in frames
    finish.assert_awaited_with(run, 'ambiguous')


def test_live_signed_callback_stores_all_tools_idempotently_and_enforces_bounds(history_api):
    api = history_api
    session = make_session(api)
    run = make_run(api, session, status='running', upstream_copy=False)
    claims = {'user': api.users[0]['id'], 'run': str(run), 'session': str(session)}

    def complete(body):
        return api.client.post('/api/v1/internal/agent/complete', json=body,
            headers={'X-Delegation': sign(api.env['DELEGATION_SECRET'], claims, 'deanery-complete')})

    try:
        body = {'status': 'done', 'answer': 'Durable attachment answer', 'tools_used': TOOLS}
        first = complete(body)
        assert first.status_code == 200, first.text
        assert complete({**body, 'answer': 'Late replacement', 'tools_used': []}).status_code == 200
        stored = api.db.execute('SELECT status,tools_used FROM backend.agent_runs WHERE id=%s', (run,)).fetchone()
        assert stored == {'status': 'done', 'tools_used': TOOLS}
        history = get_history(api, session)['messages']
        assert history[-1]['content'] == body['answer'] and history[-1]['tools_used'] == TOOLS
        for tools in (['unknown'], ['read_attachment_text']*301):
            assert complete({**body, 'tools_used': tools}).status_code == 422
            with pytest.raises(CheckViolation), api.db.transaction(force_rollback=True):
                api.db.execute('UPDATE backend.agent_runs SET tools_used=%s WHERE id=%s', (tools, run))
        with api.db.transaction(force_rollback=True):
            api.db.execute('UPDATE backend.agent_runs SET tools_used=%s WHERE id=%s', (['read_attachment_text']*300, run))
    finally:
        api.db.execute("SELECT backend.finish_agent_run(%s,%s,'failed',NULL)", (run, api.users[0]['id']))


def test_live_sse_reader_done_commits_before_terminal_event(history_api, monkeypatch):
    api = history_api
    session = make_session(api)
    run = make_run(api, session, status='running', upstream_copy=False)
    done = {'run_id': str(uuid4()), 'session_id': str(session), 'answer': 'SSE attachment saved',
            'proposals': [], 'tools_used': ['read_attachment_text']}
    response = httpx.Response(200, content=('event: done\ndata: '+json.dumps(done)+'\n\n').encode(),
                              headers={'Content-Type': 'text/event-stream'})
    monkeypatch.setattr(gateway, 'upstream_request', AsyncMock(return_value=(run, session, response, None)))
    monkeypatch.setattr(gateway, 'get_settings', lambda: SimpleNamespace(agent_timeout_seconds=10))

    async def consume():
        async with AsyncConnectionPool(api.env['DATABASE_URL'], open=False, kwargs={'row_factory': dict_row}) as pool:
            monkeypatch.setattr(gateway, 'get_pool', lambda: pool)
            stream = await gateway.chat_stream(gateway.ChatRequest(message='Read'), None, None)
            frames = []
            async for frame in stream.body_iterator:
                stored = api.db.execute('SELECT status,tools_used FROM backend.agent_runs WHERE id=%s', (run,)).fetchone()
                assert stored == {'status': 'done', 'tools_used': ['read_attachment_text']}
                frames.append(frame)
            return ''.join(frames)
    try:
        frames = asyncio.run(consume(), loop_factory=asyncio.SelectorEventLoop)
        assert 'event: done' in frames and 'event: error' not in frames
        assert get_history(api, session)['messages'][-1]['content'] == done['answer']
    finally:
        api.db.execute("SELECT backend.finish_agent_run(%s,%s,'failed',NULL)", (run, api.users[0]['id']))
