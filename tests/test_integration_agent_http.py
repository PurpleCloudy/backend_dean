"""Real Uvicorn facade + untouched original agent + deterministic HTTP model.

This verifies transport/delegation, not a live language-model evaluation.
"""
import asyncio
import json
import httpx
from test_integration_support import settings


async def main():
    env = settings()
    assert env.get('RUN_AGENT_INTEGRATION_TESTS') == '1', 'Explicit local original-agent fixture lease required'
    async with httpx.AsyncClient(base_url=env.get('BASE_URL','http://127.0.0.1:8000'), trust_env=False, timeout=60,
                                 headers={'Origin': 'http://localhost:5173'}) as client:
        login = await client.post('/api/v1/auth/login', json={'login': 'admin', 'password': env['BOOTSTRAP_PASSWORD']})
        assert login.status_code == 200, (login.status_code, login.text)
        client.headers['Authorization'] = 'Bearer '+login.json()['access_token']
        message = 'VERIFY_TOOL:' + json.dumps({'name': 'query_deanery', 'arguments': {
            'sql': 'SELECT student_id,group_id FROM deanery.student ORDER BY student_id LIMIT 5'}})
        response = await client.post('/api/v1/agent/chat', json={'message': message})
        assert response.status_code == 200, (response.status_code, response.text)
        answer = response.json()
        assert set(answer) == {'run_id', 'session_id', 'answer', 'proposals', 'tools_used'}
        assert answer['tools_used'] == ['query_deanery']
        tool_result = json.loads(json.loads(answer['answer'])['tool_results'][0])
        assert tool_result['returned'] == 5, tool_result
        print('PASS original HTTP chat + scoped SQL callback')
        events = []
        async with client.stream('POST', '/api/v1/agent/chat/stream', json={'message': message, 'session_id': answer['session_id']}) as stream:
            assert stream.status_code == 200, await stream.aread()
            raw = (await stream.aread()).decode()
        for frame in raw.split('\n\n'):
            if not frame.strip():
                continue
            lines = frame.splitlines()
            name = next(line[7:] for line in lines if line.startswith('event: '))
            value = json.loads(next(line[6:] for line in lines if line.startswith('data: ')))
            events.append((name, value))
        assert {event for event, _ in events} >= {'status', 'session', 'tool_call', 'tool_result', 'done'}, events
        assert not any(event == 'error' for event, _ in events), events
        done = events[-1][1]
        assert done['session_id'] == answer['session_id'] and done['tools_used'] == ['query_deanery']
        assert json.loads(done['answer'])['prior_user_messages'] >= 1
        assert json.loads(json.loads(done['answer'])['tool_results'][0])['returned'] == 5
        print('PASS original daemon SSE thread receives signed user/session/run; history retained')
        rejected = await client.post('/api/v1/agent/chat', json={'message': 'hello', 'session_id': '00000000-0000-0000-0000-000000000001'}, headers={'X-Actor-ID': 'user:999'})
        assert rejected.status_code == 404
        print('PASS foreign/absent session rejection')
    async with httpx.AsyncClient(base_url=env.get('TEST_AGENT_URL','http://127.0.0.1:58001'), trust_env=False, timeout=10) as upstream:
        assert (await upstream.post('/chat', json={'message': 'hello'}, headers={'X-Actor-ID': 'user:1'})).status_code == 401
        assert (await upstream.post('/sql-proposals', json={})).status_code == 404
        assert (await upstream.post('/regulations', json={})).status_code == 404
        print('PASS unsigned adapter access and legacy upload/proposal routes denied')


if __name__ == '__main__':
    asyncio.run(main())
