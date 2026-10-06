"""Async facade and per-call trusted delegation for the separate sync agent."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Literal
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from integrations.dean_agent_adapter.security import sign, verify
from .auth import require_principal, load_principal
from .config import get_settings
from .db import transaction, get_pool
from .errors import ApiError
from .schemas import json_value
from .agent_tools import ProposalSummary

router = APIRouter(tags=['agent'])
_http = None


async def start_services(app=None):
    global _http
    settings = get_settings()
    _http = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(settings.agent_timeout_seconds, connect=5),
        limits=httpx.Limits(max_connections=settings.agent_max_concurrency+2,
                           max_keepalive_connections=settings.agent_max_concurrency))


async def stop_services(app=None):
    global _http
    if _http:
        await _http.aclose()
    _http = None


async def health():
    settings = get_settings()
    if not settings.agent_url or len(settings.delegation_secret) < 32:
        return {'agent': {'status': 'unconfigured'}}
    try:
        async with asyncio.timeout(2):
            token = sign(settings.delegation_secret, {'user': 1, 'session': str(uuid4()), 'run': str(uuid4())}, 'deanery-health', 30)
            response = await _http.get(settings.agent_url.rstrip('/')+'/health/scoped', headers={'X-Delegation': token})
            response.raise_for_status()
            if response.json() != {'status': 'ready', 'adapter': 'scoped-v1'}:
                raise ValueError('Unsafe upstream')
            response = await _http.get(settings.agent_url.rstrip('/')+'/health')
            response.raise_for_status()
        return {'agent': {'status': 'ready'}}
    except Exception:
        return {'agent': {'status': 'unavailable'}}


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=8000)
    session_id: UUID | None = None


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: UUID
    session_id: UUID
    answer: str
    proposals: list[ProposalSummary]
    tools_used: list[Literal['query_deanery', 'propose_sql_change', 'search_regulations']]


class RunResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    session_id: UUID
    user_id: int
    status: Literal['running', 'ambiguous', 'done', 'failed']
    created_at: datetime
    completed_at: datetime | None


class RecoveryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: UUID
    status: Literal['failed']


async def reserve(principal, session_id, message):
    settings = get_settings()
    if not settings.agent_url or len(settings.delegation_secret) < 32:
        raise ApiError(503, 'agent_unavailable', 'Агент не настроен')
    run_id = uuid4()
    async with transaction(principal, str(run_id)) as conn:
        # Serialize admission across API workers, accounting for disconnected runs.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('deanery-agent-admission'))")
        active = await (await conn.execute("SELECT count(*) AS n FROM backend.agent_runs WHERE status IN ('running','ambiguous')")).fetchone()
        if active['n'] >= settings.agent_max_concurrency:
            raise ApiError(429, 'agent_busy', 'Агент занят; повторите позднее')
        if session_id:
            found = await (await conn.execute('SELECT id FROM public.chat_sessions WHERE id=%s AND actor_id=%s',
                (session_id, f'user:{principal.user_id}'))).fetchone()
            if not found:
                raise ApiError(404, 'session_not_found', 'Диалог не найден')
        else:
            session_id = uuid4()
            await conn.execute('INSERT INTO public.chat_sessions(id,actor_id) VALUES(%s,%s)', (session_id, f'user:{principal.user_id}'))
        active_session = await (await conn.execute("SELECT 1 FROM backend.agent_runs WHERE session_id=%s AND status IN ('running','ambiguous')", (session_id,))).fetchone()
        if active_session:
            raise ApiError(409, 'session_busy', 'Ответ в этом диалоге ещё формируется')
        await conn.execute('SELECT backend.start_agent_run(%s,%s,%s,%s)',
            (run_id, session_id, datetime.now(timezone.utc)+timedelta(seconds=settings.agent_timeout_seconds), message))
    return run_id, session_id


async def finish(run_id, status, answer=None):
    async with get_pool().connection() as conn:
        owner = await (await conn.execute('SELECT user_id FROM backend.agent_runs WHERE id=%s', (run_id,))).fetchone()
        if owner:
            await conn.execute('SELECT backend.finish_agent_run(%s,%s,%s,%s)', (run_id, owner['user_id'], status, answer))


async def upstream_request(request, body, principal, stream):
    run_id, session_id = await reserve(principal, body.session_id, body.message)
    payload = {'message': body.message, 'session_id': str(session_id)}
    raw = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode()
    token = sign(get_settings().delegation_secret, {'user': principal.user_id, 'session': str(session_id),
        'run': str(run_id), 'auth_session': str(request.state.session_id), 'body_sha256': hashlib.sha256(raw).hexdigest()},
        'dean-agent', get_settings().agent_timeout_seconds)
    req = _http.build_request('POST', get_settings().agent_url.rstrip('/') + ('/chat/stream' if stream else '/chat'),
        content=raw, headers={'Content-Type': 'application/json', 'X-Actor-ID': f'user:{principal.user_id}', 'X-Delegation': token})
    try:
        response = await _http.send(req, stream=True)
        if response.status_code >= 400:
            await response.aclose()
            await finish(run_id, 'failed')
            raise ApiError(503 if response.status_code == 503 else 502, 'agent_error', 'Агент не выполнил запрос')
        return run_id, session_id, response
    except (httpx.HTTPError, asyncio.CancelledError) as exc:
        definite = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
        await asyncio.shield(finish(run_id, 'failed' if definite else 'ambiguous'))
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise ApiError(504 if isinstance(exc, httpx.TimeoutException) else 502,
            'agent_unreachable' if definite else 'agent_outcome_unknown',
            'Агент недоступен' if definite else 'Ответ не получен; выполнение может продолжаться',
            {'run_id': str(run_id), 'session_id': str(session_id)}) from None


def check_answer(value, session_id):
    if not isinstance(value, dict) or set(value) != {'run_id', 'session_id', 'answer', 'proposals', 'tools_used'} or value['session_id'] != str(session_id):
        raise ApiError(502, 'agent_protocol_error', 'Неверный ответ агента')
    try:
        UUID(value['run_id'])
    except (ValueError, TypeError) as exc:
        raise ApiError(502, 'agent_protocol_error', 'Неверный ответ агента') from exc
    if not isinstance(value['answer'], str) or len(value['answer']) > 100000 or not isinstance(value['proposals'], list) or not isinstance(value['tools_used'], list):
        raise ApiError(502, 'agent_protocol_error', 'Неверный ответ агента')
    return value


@router.post('/agent/chat', responses={200: {'model': ChatResponse}})
async def chat(body: ChatRequest, request: Request, principal=Depends(require_principal)):
    run_id, session_id, response = await upstream_request(request, body, principal, False)
    try:
        raw = bytearray()
        async with asyncio.timeout(get_settings().agent_timeout_seconds):
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 1_048_576:
                    raise ValueError('Agent response size limit')
        result = check_answer(json.loads(raw), session_id)
        await finish(run_id, 'done', result['answer'])
        return result
    except (ValueError, ApiError, TimeoutError, httpx.HTTPError):
        await finish(run_id, 'ambiguous')
        raise ApiError(502, 'agent_protocol_error', 'Неверный ответ агента',
                       {'run_id': str(run_id), 'session_id': str(session_id)}) from None
    finally:
        await response.aclose()


@router.post('/agent/chat/stream', response_class=StreamingResponse,
    responses={200: {'description': 'UTF-8 SSE frames: status, session, tool_call, tool_result, done or error. Each data line is JSON; done has the ChatResponse shape. An error may include run_id/session_id when the upstream outcome is unknown.',
        'content': {'text/event-stream': {'schema': {'type': 'string'}, 'example': 'event: session\ndata: {"session_id":"00000000-0000-0000-0000-000000000001"}\n\n'}}}})
async def chat_stream(body: ChatRequest, request: Request, principal=Depends(require_principal)):
    run_id, session_id, response = await upstream_request(request, body, principal, True)
    if not response.headers.get('content-type', '').startswith('text/event-stream'):
        await response.aclose()
        await finish(run_id, 'ambiguous')
        raise ApiError(502, 'agent_protocol_error', 'Неверный поток агента')

    async def events():
        terminal = False
        buffer = b''
        received = 0
        try:
            async with asyncio.timeout(get_settings().agent_timeout_seconds):
                async for chunk in response.aiter_bytes():
                    buffer += chunk
                    received += len(chunk)
                    if received > 2_097_152:
                        raise ValueError('SSE total limit')
                    while b'\n\n' in buffer:
                        frame, buffer = buffer.split(b'\n\n', 1)
                        if len(frame) > 131072:
                            raise ValueError('SSE frame limit')
                        lines = frame.decode('utf-8').splitlines()
                        event = next((line[7:] for line in lines if line.startswith('event: ')), '')
                        value = json.loads('\n'.join(line[6:] for line in lines if line.startswith('data: ')))
                        if event not in {'status', 'session', 'tool_call', 'tool_result', 'done', 'error'} or not isinstance(value, dict):
                            raise ValueError('SSE protocol')
                        if event == 'session' and value.get('session_id') != str(session_id):
                            raise ValueError('SSE session')
                        if event == 'done':
                            check_answer(value, session_id)
                            terminal = True
                            await finish(run_id, 'done', value['answer'])
                        if event == 'error':
                            value = {'type': 'AgentError', 'message': 'Агент не ответил'}
                            terminal = True
                            await finish(run_id, 'failed')
                        yield 'event: ' + event + '\ndata: ' + json.dumps(value, ensure_ascii=False) + '\n\n'
                        if terminal:
                            return
                    if len(buffer) > 131072:
                        raise ValueError('SSE frame limit')
                if not terminal:
                    raise ValueError('SSE incomplete')
        except (httpx.HTTPError, TimeoutError, ValueError, ApiError):
            value = {'type': 'GatewayError', 'message': 'Ответ не получен; выполнение может продолжаться',
                     'run_id': str(run_id), 'session_id': str(session_id)}
            yield 'event: error\ndata: ' + json.dumps(value, ensure_ascii=False) + '\n\n'
        finally:
            await response.aclose()
            if not terminal:
                await asyncio.shield(finish(run_id, 'ambiguous'))
    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


async def delegated(request, audience, permit_finished=False):
    try:
        claims = verify(get_settings().delegation_secret, request.headers.get('x-delegation', ''), audience)
    except ValueError:
        raise ApiError(401, 'invalid_delegation', 'Недействительное делегирование') from None
    async with get_pool().connection() as conn:
        async with conn.transaction():
            run = await (await conn.execute('SELECT * FROM backend.agent_runs WHERE id=%s AND user_id=%s AND session_id=%s FOR UPDATE',
                (UUID(claims['run']), claims['user'], UUID(claims['session'])))).fetchone()
            if not run or not permit_finished and (run['status'] not in ('running', 'ambiguous') or run['expires_at'] <= datetime.now(timezone.utc)):
                raise ApiError(401, 'invalid_delegation', 'Запуск завершён или не найден')
            if not permit_finished:
                auth_session = await (await conn.execute('SELECT 1 FROM backend.sessions WHERE id=%s AND user_id=%s AND revoked_at IS NULL AND expires_at>now()',
                    (UUID(claims.get('auth_session', '')), claims['user']))).fetchone()
                if not auth_session:
                    raise ApiError(401, 'inactive_session', 'Сеанс отозван')
            try:
                await conn.execute('INSERT INTO backend.delegation_nonces(nonce,expires_at) VALUES(%s,%s)',
                    (UUID(claims['nonce']), datetime.fromtimestamp(claims['exp'], timezone.utc)))
            except UniqueViolation:
                raise ApiError(401, 'delegation_replayed', 'Повторное делегирование запрещено') from None
            if claims.get('upstream_run'):
                upstream_run = UUID(claims['upstream_run'])
                if run['upstream_run_id'] and run['upstream_run_id'] != upstream_run:
                    raise ApiError(401, 'run_mismatch', 'Запуск не совпадает')
                await conn.execute('UPDATE backend.agent_runs SET upstream_run_id=%s WHERE id=%s', (upstream_run, run['id']))
            principal = await load_principal(conn, claims['user']) if not permit_finished else None
    return claims, principal


@router.post('/internal/agent/claim', include_in_schema=False)
async def claim(request: Request):
    claims, _ = await delegated(request, 'dean-agent')
    return {'run': claims['run'], 'session': claims['session']}


class ToolCall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Literal['query_deanery', 'propose_sql_change', 'search_regulations', 'bind']
    arguments: dict = Field(default_factory=dict)


@router.post('/internal/agent/tool', include_in_schema=False)
async def tool(body: ToolCall, request: Request):
    claims, principal = await delegated(request, 'deanery-tools')
    if len(json.dumps(body.arguments)) > 12000:
        raise ApiError(422, 'tool_input_limit', 'Слишком большой запрос')
    from .agent_tools import query_deanery, create_proposal, search_regulations
    if body.name == 'bind':
        return {'bound': True}
    expected = {'query_deanery': {'sql'}, 'propose_sql_change': {'sql', 'explanation', 'reason'}, 'search_regulations': {'query'}}[body.name]
    if set(body.arguments) != expected or not all(isinstance(v, str) for v in body.arguments.values()):
        raise ApiError(422, 'invalid_tool_arguments', 'Недопустимые параметры инструмента')
    async with transaction(principal, claims['run'], readonly=body.name == 'query_deanery') as conn:
        if body.name == 'query_deanery':
            return await query_deanery(conn, principal, **body.arguments)
        if body.name == 'propose_sql_change':
            return await create_proposal(conn, principal, **body.arguments,
                session_id=UUID(claims['session']), run_id=claims['upstream_run'])
        return await search_regulations(conn, principal, **body.arguments)


class Completion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['done', 'failed']
    answer: str | None = Field(default=None, max_length=100000)


@router.post('/internal/agent/complete', include_in_schema=False)
async def complete(body: Completion, request: Request):
    claims, _ = await delegated(request, 'deanery-complete', permit_finished=True)
    if (body.status == 'done') != (body.answer is not None):
        raise ApiError(422, 'invalid_completion', 'Ответ не соответствует результату запуска')
    await finish(UUID(claims['run']), body.status, body.answer)
    return {'accepted': True}


class RunRecovery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    instance_terminated: Literal[True]
    reason: str = Field(min_length=10, max_length=1000)


@router.get('/agent/runs/{run_id}', responses={200: {'model': RunResponse}})
async def run_status(run_id: UUID, principal=Depends(require_principal)):
    async with transaction(principal) as conn:
        row = await (await conn.execute('SELECT id,session_id,user_id,status,created_at,completed_at FROM backend.agent_runs WHERE id=%s', (run_id,))).fetchone()
        if not row or row['user_id'] != principal.user_id and 'admin' not in principal.roles:
            raise ApiError(404, 'run_not_found', 'Запуск не найден')
        return row


@router.post('/agent/runs/{run_id}/recover', responses={200: {'model': RecoveryResponse}})
async def recover_run(run_id: UUID, body: RunRecovery, principal=Depends(require_principal)):
    if len(body.reason.strip()) < 10:
        raise ApiError(422, 'reason_required', 'Укажите причину восстановления')
    async with transaction(principal) as conn:
        principal = await load_principal(conn, principal.user_id)
        if 'admin' not in principal.roles:
            raise ApiError(403, 'forbidden', 'Восстановление выполняет администратор')
        row = await (await conn.execute('SELECT * FROM backend.agent_runs WHERE id=%s FOR UPDATE', (run_id,))).fetchone()
        if not row:
            raise ApiError(404, 'run_not_found', 'Запуск не найден')
        if row['status'] not in ('running', 'ambiguous'):
            raise ApiError(409, 'run_already_finished', 'Запуск уже завершён')
        await conn.execute("INSERT INTO backend.events(user_id,action,resource,record_key) VALUES(%s,'recover_terminated_agent','agent_run',%s)",
            (principal.user_id, Jsonb({'run_id': str(run_id), 'session_id': str(row['session_id']),
                'owner_id': row['user_id'], 'before': row['status'], 'after': 'failed',
                'instance_terminated': True, 'reason': body.reason.strip()})))
        await conn.execute("SELECT backend.finish_agent_run(%s,%s,'failed',NULL)", (run_id, row['user_id']))
        return {'run_id': run_id, 'status': 'failed'}
