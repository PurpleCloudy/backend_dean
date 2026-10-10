"""Async facade and per-call trusted delegation for the separate sync agent."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Literal
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from psycopg import Error as DatabaseError
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from integrations.dean_agent_adapter.security import sign, verify
from .auth import require_principal, load_principal
from .config import get_settings
from .db import transaction, get_pool
from .errors import ApiError, ErrorEnvelope
from .schemas import json_value
from .agent_tools import ProposalSummary, ProposalResponse, proposal_row
from .files import get_version, download_file
from .jobs import ParseQuality

router = APIRouter(tags=['agent'])
_http = None
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 20_000_000
MAX_TEXT_CHUNKS = 50
ATTACHED_MESSAGE_CHARS = 2000
TEXT_MIMES = ['text/plain', 'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'text/csv']
IMAGE_MIMES = ['image/png', 'image/jpeg', 'image/webp']
AgentToolName = Literal['query_deanery', 'propose_sql_change', 'search_regulations', 'read_attachment_text']


class AdapterCapabilities(BaseModel):
    model_config = ConfigDict(extra='forbid')
    attachments_context_v1: StrictBool
    attachments_vision_v1: StrictBool = False
    model: str | None = Field(default=None, max_length=200)


class AdapterHealth(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['ready']
    adapter: Literal['scoped-v1']
    capabilities: AdapterCapabilities | None = None


class AttachmentRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    file_id: UUID
    version_id: UUID


class AttachmentMetadata(AttachmentRef):
    ordinal: int = Field(ge=0, lt=MAX_ATTACHMENTS)
    title: str
    source: str
    filename: str
    mime: Literal['text/plain', 'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'text/csv', 'image/png', 'image/jpeg', 'image/webp']
    byte_size: int
    sha256: str
    quality: ParseQuality | None
    text_available: bool


class AttachmentContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    backend_run_id: UUID
    canonical_request_id: int
    session_id: UUID
    attachments: list[AttachmentMetadata]


class AttachmentChunk(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ordinal: int
    page: int | None
    text: str


class AttachmentText(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_id: UUID
    chunks: list[AttachmentChunk]
    next_offset: int | None
    quality: ParseQuality | None
    text_available: bool


class AttachmentCapability(BaseModel):
    model_config = ConfigDict(extra='forbid')
    protocol: Literal['v1'] = 'v1'
    backend_supported: Literal[True] = True
    agent_supported: bool
    agent_status: Literal['ready', 'unavailable']
    max_attachments: int = MAX_ATTACHMENTS
    max_total_bytes: int = MAX_ATTACHMENT_BYTES
    max_text_chunks: int = MAX_TEXT_CHUNKS
    vision_supported: bool = False
    model: str | None = None
    accepted_mime_types: list[str] = Field(default_factory=list)
    upload_supported_mime_types: list[str] = Field(default_factory=lambda: TEXT_MIMES + IMAGE_MIMES)
    max_file_bytes: int = 10_000_000
    max_message_chars: int = ATTACHED_MESSAGE_CHARS
    max_visual_pages: int = 10
    max_initial_text_chunks_per_file: int = 1
    max_tool_text_chunks: int = 2


class CapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    attachment_context: AttachmentCapability


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


async def adapter_capabilities():
    settings = get_settings()
    if not settings.agent_url or len(settings.delegation_secret) < 32:
        raise ApiError(503, 'agent_unavailable', 'Агент не настроен')
    try:
        async with asyncio.timeout(2):
            token = sign(settings.delegation_secret, {'user': 1, 'session': str(uuid4()), 'run': str(uuid4())}, 'deanery-health', 30)
            async with _http.stream('GET', settings.agent_url.rstrip('/')+'/health/scoped', headers={'X-Delegation': token}) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 4096:
                        raise ValueError('Capability response limit')
            value = AdapterHealth.model_validate_json(raw)
            if 'capabilities' in value.model_fields_set and value.capabilities is None:
                raise ValueError('Malformed capabilities')
            profile = value.capabilities or AdapterCapabilities(attachments_context_v1=False)
            if profile.attachments_vision_v1 and (not profile.attachments_context_v1 or not profile.model):
                raise ValueError('Visual profile needs a model and attachment support')
            return profile
    except (httpx.HTTPError, ValueError, TimeoutError):
        raise ApiError(503, 'agent_unavailable', 'Возможности агента недоступны') from None


async def health():
    settings = get_settings()
    if not settings.agent_url or len(settings.delegation_secret) < 32:
        return {'agent': {'status': 'unconfigured'}}
    try:
        await adapter_capabilities()
        async with asyncio.timeout(2):
            response = await _http.get(settings.agent_url.rstrip('/')+'/health')
            response.raise_for_status()
        return {'agent': {'status': 'ready'}}
    except Exception:
        return {'agent': {'status': 'unavailable'}}


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=8000)
    session_id: UUID | None = None
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)

    @model_validator(mode='after')
    def unique_versions(self):
        if not self.message.strip():
            raise ApiError(422, 'message_required', 'Введите сообщение')
        if len({item.version_id for item in self.attachments}) != len(self.attachments):
            raise ApiError(422, 'duplicate_attachment', 'Версия файла указана повторно')
        if self.attachments and len(self.message) > ATTACHED_MESSAGE_CHARS:
            raise ApiError(422, 'attachment_message_too_long', 'Сообщение с вложениями ограничено 2000 символами')
        return self


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: UUID
    session_id: UUID
    answer: str
    proposals: list[ProposalSummary]
    tools_used: list[AgentToolName]
    attachment_context: AttachmentContext | None = None


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


class SessionSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    preview: str
    busy: bool


class SessionList(BaseModel):
    items: list[SessionSummary]
    next_offset: int | None


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    role: Literal['user', 'assistant', 'error']
    content: str
    created_at: datetime
    run_id: UUID | None
    status: Literal['running', 'done', 'failed', 'ambiguous'] | None
    request_id: UUID | None
    attachments: list[AttachmentMetadata] = Field(default_factory=list)
    unavailable_attachment_count: int = 0
    proposals: list[ProposalResponse] = Field(default_factory=list)
    unavailable_proposal_count: int = 0
    tools_used: list[AgentToolName] = Field(default_factory=list)


class SessionHistory(SessionSummary):
    messages: list[HistoryMessage]
    next_offset: int | None


class RenameSession(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)


SESSION_SUMMARY_SQL = """
SELECT s.id,coalesce(s.title,left(coalesce(first_run.request_text,first_message.content,'Новый диалог'),80)) AS title,
 s.created_at,greatest(s.created_at,last_run.updated_at,last_message.created_at) AS updated_at,
 left(coalesce(last_run.response_text,last_run.request_text,last_message.content,''),240) AS preview,
 EXISTS(SELECT 1 FROM backend.agent_runs r WHERE r.session_id=s.id AND r.status IN('running','ambiguous')) AS busy
FROM public.chat_sessions s
LEFT JOIN LATERAL (
 SELECT q.request_text FROM backend.agent_runs r JOIN deanery.agent_request q ON q.agent_request_id=r.canonical_request_id
 WHERE r.session_id=s.id AND r.user_id=%(user_id)s ORDER BY r.created_at,r.id LIMIT 1
) first_run ON true
LEFT JOIN LATERAL (
 SELECT q.request_text,q.response_text,greatest(r.created_at,r.completed_at) AS updated_at
 FROM backend.agent_runs r JOIN deanery.agent_request q ON q.agent_request_id=r.canonical_request_id
 WHERE r.session_id=s.id AND r.user_id=%(user_id)s ORDER BY r.created_at DESC,r.id DESC LIMIT 1
) last_run ON true
LEFT JOIN LATERAL (SELECT content FROM public.chat_messages WHERE session_id=s.id AND role='user' ORDER BY id LIMIT 1) first_message ON true
LEFT JOIN LATERAL (SELECT content,created_at FROM public.chat_messages WHERE session_id=s.id ORDER BY id DESC LIMIT 1) last_message ON true
WHERE s.actor_id=%(actor_id)s AND s.deleted_at IS NULL
"""


async def owned_session(conn, principal, session_id, lock=False):
    row = await (await conn.execute('SELECT id FROM public.chat_sessions WHERE id=%s AND actor_id=%s AND deleted_at IS NULL'
        + (' FOR UPDATE' if lock else ''), (session_id, f'user:{principal.user_id}'))).fetchone()
    if row is None:
        raise ApiError(404, 'session_not_found', 'Диалог не найден')


async def session_summary(conn, principal, session_id):
    row = await (await conn.execute(SESSION_SUMMARY_SQL + ' AND s.id=%(session_id)s',
        {'user_id': principal.user_id, 'actor_id': f'user:{principal.user_id}', 'session_id': session_id})).fetchone()
    if row is None:
        raise ApiError(404, 'session_not_found', 'Диалог не найден')
    return row


@router.get('/agent/sessions', response_model=SessionList)
async def list_sessions(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000),
                        principal=Depends(require_principal)):
    async with transaction(principal, readonly=True) as conn:
        rows = await (await conn.execute(SESSION_SUMMARY_SQL + ' ORDER BY updated_at DESC,s.id DESC LIMIT %(limit)s OFFSET %(offset)s',
            {'user_id': principal.user_id, 'actor_id': f'user:{principal.user_id}', 'limit': limit+1, 'offset': offset})).fetchall()
        return {'items': rows[:limit], 'next_offset': offset+limit if len(rows)>limit else None}


@router.get('/agent/sessions/{session_id}', response_model=SessionHistory)
async def session_history(session_id: UUID, limit: int = Query(100, ge=1, le=200),
                          offset: int = Query(0, ge=0, le=1000000), principal=Depends(require_principal)):
    # Proposal visibility uses the existing review helper, including its rollback-only row locks.
    async with transaction(principal) as conn:
        summary = await session_summary(conn, principal, session_id)
        rows = await (await conn.execute("""
WITH managed AS (
 SELECT r.*,q.request_text,q.response_text FROM backend.agent_runs r
 JOIN deanery.agent_request q ON q.agent_request_id=r.canonical_request_id
 WHERE r.session_id=%(session_id)s AND r.user_id=%(user_id)s
), messages AS (
 SELECT 'run:'||id||':user' AS id,'user' AS role,request_text AS content,created_at,
  id AS run_id,status,created_at AS turn_at,id::text AS turn_key,0 AS position FROM managed
 UNION ALL
 SELECT 'run:'||id||':reply',CASE WHEN status='done' THEN 'assistant' ELSE 'error' END,
  CASE status WHEN 'done' THEN response_text WHEN 'failed' THEN 'Агент не выполнил запрос.'
   ELSE 'Ответ не получен; выполнение может продолжаться. Не отправляйте запрос повторно.' END,
  coalesce(completed_at,created_at),id,status,created_at,id::text,1 FROM managed WHERE status<>'running'
 UNION ALL
 SELECT 'legacy:'||m.id,m.role,m.content,m.created_at,NULL,NULL,m.created_at,lpad(m.id::text,20,'0'),0
 FROM public.chat_messages m WHERE m.session_id=%(session_id)s AND m.role IN('user','assistant')
 AND m.created_at < coalesce((SELECT min(created_at) FROM managed),'infinity'::timestamptz)
)
SELECT id,role,content,created_at,run_id,status,run_id AS request_id FROM messages
ORDER BY turn_at,turn_key,position LIMIT %(limit)s OFFSET %(offset)s
""", {'session_id': session_id, 'user_id': principal.user_id, 'limit': limit+1, 'offset': offset})).fetchall()
        messages = [HistoryMessage(**row) for row in rows[:limit]]
        for message in messages:
            if message.run_id is None:
                continue
            if message.role == 'user':
                refs = await (await conn.execute('SELECT file_id,version_id,ordinal FROM backend.agent_run_attachments WHERE run_id=%s ORDER BY ordinal',
                    (message.run_id,))).fetchall()
                for ref in refs:
                    try:
                        message.attachments.append(await attachment_metadata(conn, principal,
                            AttachmentRef(file_id=ref['file_id'], version_id=ref['version_id']), ref['ordinal']))
                    except ApiError as exc:
                        if exc.status not in (403, 404, 409):
                            raise
                        message.unavailable_attachment_count += 1
            else:
                run = await (await conn.execute('SELECT tools_used,upstream_run_id FROM backend.agent_runs WHERE id=%s', (message.run_id,))).fetchone()
                message.tools_used = run['tools_used'] or []
                proposals = await (await conn.execute('SELECT id FROM public.sql_change_proposals WHERE session_id=%s AND owner_id=%s '
                    'AND agent_run_id IN (%s,%s) ORDER BY created_at,id',
                    (session_id, principal.user_id, str(message.run_id), str(run['upstream_run_id'])))).fetchall()
                for proposal in proposals:
                    try:
                        row, _ = await proposal_row(conn, principal, proposal['id'])
                        message.proposals.append(ProposalResponse(**{key: row[key] for key in ProposalResponse.model_fields}))
                    except ApiError as exc:
                        if exc.status not in (401, 403, 404):
                            raise
                        message.unavailable_proposal_count += 1
        return {**summary, 'messages': messages, 'next_offset': offset+limit if len(rows)>limit else None}


@router.patch('/agent/sessions/{session_id}', response_model=SessionSummary)
async def rename_session(session_id: UUID, body: RenameSession, principal=Depends(require_principal)):
    async with transaction(principal) as conn:
        await owned_session(conn, principal, session_id, lock=True)
        await conn.execute('UPDATE public.chat_sessions SET title=%s WHERE id=%s', (body.title, session_id))
        return await session_summary(conn, principal, session_id)


@router.delete('/agent/sessions/{session_id}', status_code=204)
async def delete_session(session_id: UUID, principal=Depends(require_principal)):
    async with transaction(principal) as conn:
        await owned_session(conn, principal, session_id, lock=True)
        active = await (await conn.execute("SELECT 1 FROM backend.agent_runs WHERE session_id=%s AND status IN('running','ambiguous')", (session_id,))).fetchone()
        if active:
            raise ApiError(409, 'session_busy', 'Диалог нельзя удалить, пока выполнение не завершено')
        await conn.execute('UPDATE public.chat_sessions SET deleted_at=clock_timestamp() WHERE id=%s', (session_id,))
        return Response(status_code=204)


async def attachment_metadata(conn, principal, reference, ordinal):
    metadata, version = await get_version(conn, principal, reference.file_id, reference.version_id, ready=True)
    available = await (await conn.execute('SELECT EXISTS(SELECT 1 FROM backend.file_chunks WHERE version_id=%s) AS value',
        (reference.version_id,))).fetchone()
    quality = ParseQuality.model_validate(version['quality']) if 'ocr_performed' in version['quality'] else None
    return AttachmentMetadata(file_id=reference.file_id, version_id=reference.version_id, ordinal=ordinal,
        title=metadata['title'], source=metadata['source'],
        **{key: version[key] for key in ('filename', 'mime', 'byte_size', 'sha256')},
        quality=quality, text_available=available['value'])


async def reserve(principal, session_id, message, attachments=(), *, vision=False):
    settings = get_settings()
    if not settings.agent_url or len(settings.delegation_secret) < 32:
        raise ApiError(503, 'agent_unavailable', 'Агент не настроен')
    run_id = uuid4()
    async with transaction(principal, str(run_id)) as conn:
        principal = await load_principal(conn, principal.user_id)
        metadata = [await attachment_metadata(conn, principal, item, ordinal) for ordinal, item in enumerate(attachments)]
        if sum(item.byte_size for item in metadata) > MAX_ATTACHMENT_BYTES:
            raise ApiError(413, 'attachments_too_large', 'Суммарный размер вложений превышает 20 МБ')
        if not vision and any(item.mime in IMAGE_MIMES or item.mime == 'application/pdf' and not item.text_available for item in metadata):
            raise ApiError(412, 'agent_vision_unsupported', 'Текущая модель не читает изображения и PDF без текстового слоя')
        # Serialize admission across API workers, accounting for disconnected runs.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('deanery-agent-admission'))")
        active = await (await conn.execute("SELECT count(*) AS n FROM backend.agent_runs WHERE status IN ('running','ambiguous')")).fetchone()
        if active['n'] >= settings.agent_max_concurrency:
            raise ApiError(429, 'agent_busy', 'Агент занят; повторите позднее')
        if session_id:
            found = await (await conn.execute('SELECT id FROM public.chat_sessions WHERE id=%s AND actor_id=%s AND deleted_at IS NULL',
                (session_id, f'user:{principal.user_id}'))).fetchone()
            if not found:
                raise ApiError(404, 'session_not_found', 'Диалог не найден')
        else:
            session_id = uuid4()
            await conn.execute('INSERT INTO public.chat_sessions(id,actor_id) VALUES(%s,%s)', (session_id, f'user:{principal.user_id}'))
        active_session = await (await conn.execute("SELECT 1 FROM backend.agent_runs WHERE session_id=%s AND status IN ('running','ambiguous')", (session_id,))).fetchone()
        if active_session:
            raise ApiError(409, 'session_busy', 'Ответ в этом диалоге ещё формируется')
        canonical = await (await conn.execute('SELECT backend.start_agent_run(%s,%s,%s,%s) AS id',
            (run_id, session_id, datetime.now(timezone.utc)+timedelta(seconds=settings.agent_timeout_seconds), message))).fetchone()
        for item in metadata:
            await conn.execute('INSERT INTO backend.agent_run_attachments(run_id,file_id,version_id,ordinal) VALUES(%s,%s,%s,%s)',
                (run_id, item.file_id, item.version_id, item.ordinal))
        context = AttachmentContext(backend_run_id=run_id, canonical_request_id=canonical['id'], session_id=session_id,
            attachments=metadata).model_dump(mode='json') if metadata else None
    return run_id, session_id, context


async def finish(run_id, status, answer=None, tools_used=None):
    async with get_pool().connection() as conn:
        owner = await (await conn.execute('SELECT user_id FROM backend.agent_runs WHERE id=%s', (run_id,))).fetchone()
        if owner:
            await conn.execute('SELECT backend.finish_agent_run(%s,%s,%s,%s)', (run_id, owner['user_id'], status, answer))
            if tools_used is not None and status == 'done':
                # Legacy callbacks omit metadata; the first supplied trace fills it exactly once.
                await conn.execute("UPDATE backend.agent_runs SET tools_used=%s WHERE id=%s AND status='done' AND tools_used IS NULL",
                    (tools_used, run_id))


async def upstream_request(request, body, principal, stream):
    profile = await adapter_capabilities() if body.attachments else None
    if profile is not None and not profile.attachments_context_v1:
        raise ApiError(412, 'agent_attachments_unsupported', 'Текущий агент ещё не поддерживает вложения')
    run_id, session_id, context = await reserve(principal, body.session_id, body.message, body.attachments,
        vision=bool(profile and profile.attachments_vision_v1))
    payload = {'message': body.message, 'session_id': str(session_id)}
    if body.attachments:
        payload['attachments'] = [item.model_dump(mode='json') for item in body.attachments]
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
            raise ApiError(503 if response.status_code == 503 else 502, 'agent_error', 'Агент не выполнил запрос',
                {'run_id': str(run_id), 'session_id': str(session_id)})
        return run_id, session_id, response, context
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
    ChatResponse.model_validate(value)
    if len(value['tools_used']) > 300:
        raise ApiError(502, 'agent_protocol_error', 'Слишком много вызовов инструментов')
    return value


@router.post('/agent/chat', responses={200: {'model': ChatResponse}, 412: {'model': ErrorEnvelope, 'description': 'Installed agent cannot consume attachment context; nothing admitted.'}})
async def chat(body: ChatRequest, request: Request, principal=Depends(require_principal)):
    run_id, session_id, response, context = await upstream_request(request, body, principal, False)
    try:
        raw = bytearray()
        async with asyncio.timeout(get_settings().agent_timeout_seconds):
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 1_048_576:
                    raise ValueError('Agent response size limit')
        result = check_answer(json.loads(raw), session_id)
        await finish(run_id, 'done', result['answer'], result['tools_used'])
        if context is not None:
            result['attachment_context'] = context
        return result
    except asyncio.CancelledError:
        await asyncio.shield(finish(run_id, 'ambiguous'))
        raise
    except (ValueError, ApiError, TimeoutError, httpx.HTTPError):
        await finish(run_id, 'ambiguous')
        raise ApiError(502, 'agent_protocol_error', 'Неверный ответ агента',
                       {'run_id': str(run_id), 'session_id': str(session_id)}) from None
    finally:
        await response.aclose()


@router.post('/agent/chat/stream', response_class=StreamingResponse,
    responses={200: {'description': 'UTF-8 SSE frames: status, session, tool_call, tool_result, done or error. Each data line is JSON; done has the ChatResponse shape. Attached requests add attachment_context to session and done. Its backend_run_id is distinct from the upstream run_id. An error may include backend run_id/session_id when the upstream outcome is unknown.',
        'content': {'text/event-stream': {'schema': {'type': 'string'}, 'example': 'event: session\ndata: {"session_id":"00000000-0000-0000-0000-000000000001"}\n\n'}}},
        412: {'model': ErrorEnvelope, 'description': 'Installed agent cannot consume attachment context; nothing admitted.'}})
async def chat_stream(body: ChatRequest, request: Request, principal=Depends(require_principal)):
    run_id, session_id, response, context = await upstream_request(request, body, principal, True)
    if not response.headers.get('content-type', '').startswith('text/event-stream'):
        await response.aclose()
        await finish(run_id, 'ambiguous')
        raise ApiError(502, 'agent_protocol_error', 'Неверный поток агента',
            {'run_id': str(run_id), 'session_id': str(session_id)})

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
                            await finish(run_id, 'done', value['answer'], value['tools_used'])
                            terminal = True
                        if event == 'error':
                            value = {'type': 'AgentError', 'message': 'Агент не ответил'}
                            value.update(run_id=str(run_id), session_id=str(session_id))
                            await finish(run_id, 'failed')
                            terminal = True
                        if context is not None and event in {'session', 'done'}:
                            value['attachment_context'] = context
                        yield 'event: ' + event + '\ndata: ' + json.dumps(value, ensure_ascii=False) + '\n\n'
                        if terminal:
                            return
                    if len(buffer) > 131072:
                        raise ValueError('SSE frame limit')
                if not terminal:
                    raise ValueError('SSE incomplete')
        except (httpx.HTTPError, TimeoutError, ValueError, ApiError, DatabaseError):
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


@router.get('/agent/capabilities', responses={200: {'model': CapabilitiesResponse}})
async def capabilities(principal=Depends(require_principal)):
    try:
        profile = await adapter_capabilities()
        status = 'ready'
    except ApiError:
        profile, status = AdapterCapabilities(attachments_context_v1=False), 'unavailable'
    supported = profile.attachments_context_v1
    visual = supported and profile.attachments_vision_v1
    return CapabilitiesResponse(attachment_context=AttachmentCapability(
        agent_supported=supported, agent_status=status, vision_supported=visual, model=profile.model,
        accepted_mime_types=(TEXT_MIMES + (IMAGE_MIMES if visual else [])) if supported else []))


async def run_attachment_context(conn, principal, run_id):
    run = await (await conn.execute('SELECT id,session_id,canonical_request_id FROM backend.agent_runs WHERE id=%s AND user_id=%s',
        (run_id, principal.user_id))).fetchone()
    if not run:
        raise ApiError(404, 'run_not_found', 'Запуск не найден')
    rows = await (await conn.execute('SELECT file_id,version_id,ordinal FROM backend.agent_run_attachments WHERE run_id=%s ORDER BY ordinal',
        (run_id,))).fetchall()
    metadata = [await attachment_metadata(conn, principal, AttachmentRef(file_id=row['file_id'], version_id=row['version_id']), row['ordinal']) for row in rows]
    return AttachmentContext(backend_run_id=run['id'], canonical_request_id=run['canonical_request_id'],
        session_id=run['session_id'], attachments=metadata)


@router.get('/agent/runs/{run_id}/attachments', responses={200: {'model': AttachmentContext}},
    description='Owner-only attachment metadata for the canonical backend_run_id, not the upstream run_id. Every read rechecks current file access and readiness.')
async def attachments_for_run(run_id: UUID, principal=Depends(require_principal)):
    async with transaction(principal, readonly=True) as conn:
        principal = await load_principal(conn, principal.user_id)
        return await run_attachment_context(conn, principal, run_id)


@router.get('/internal/agent/attachments', include_in_schema=False)
async def delegated_attachments(request: Request):
    claims, principal = await delegated(request, 'deanery-attachments')
    async with transaction(principal, claims['run'], readonly=True) as conn:
        principal = await load_principal(conn, principal.user_id)
        return await run_attachment_context(conn, principal, UUID(claims['run']))


async def bound_attachment(conn, principal, run_id, version_id):
    row = await (await conn.execute('SELECT a.file_id,a.ordinal FROM backend.agent_run_attachments a '
        'JOIN backend.agent_runs r ON r.id=a.run_id WHERE a.run_id=%s AND a.version_id=%s AND r.user_id=%s',
        (run_id, version_id, principal.user_id))).fetchone()
    if not row:
        raise ApiError(404, 'attachment_not_found', 'Вложение этого запроса не найдено')
    return AttachmentRef(file_id=row['file_id'], version_id=version_id), row['ordinal']


@router.get('/internal/agent/attachments/{version_id}/text', include_in_schema=False)
async def delegated_attachment_text(version_id: UUID, request: Request,
    offset: int = Query(default=0, ge=0, le=10000), limit: int = Query(default=20, ge=1, le=MAX_TEXT_CHUNKS)):
    claims, principal = await delegated(request, 'deanery-attachments')
    async with transaction(principal, claims['run'], readonly=True) as conn:
        principal = await load_principal(conn, principal.user_id)
        reference, ordinal = await bound_attachment(conn, principal, UUID(claims['run']), version_id)
        metadata = await attachment_metadata(conn, principal, reference, ordinal)
        rows = await (await conn.execute('SELECT ordinal,page,content AS text FROM backend.file_chunks '
            'WHERE version_id=%s ORDER BY ordinal LIMIT %s OFFSET %s', (version_id, limit+1, offset))).fetchall()
        return AttachmentText(version_id=version_id, chunks=rows[:limit], next_offset=offset+limit if len(rows)>limit else None,
            quality=metadata.quality, text_available=metadata.text_available)


@router.get('/internal/agent/attachments/{version_id}/download', response_class=StreamingResponse, include_in_schema=False)
async def delegated_attachment_download(version_id: UUID, request: Request):
    claims, principal = await delegated(request, 'deanery-attachments')
    async with transaction(principal, claims['run'], readonly=True) as conn:
        principal = await load_principal(conn, principal.user_id)
        reference, _ = await bound_attachment(conn, principal, UUID(claims['run']), version_id)
    # Reuse the normal private download: current ACL/ready, server-owned S3 key,
    # bounded chunk streaming and existing safe download headers. No signed URL.
    return await download_file(reference.file_id, reference.version_id, principal)


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
    tools_used: list[AgentToolName] | None = Field(default=None, max_length=300)


@router.post('/internal/agent/complete', include_in_schema=False)
async def complete(body: Completion, request: Request):
    claims, _ = await delegated(request, 'deanery-complete', permit_finished=True)
    if (body.status == 'done') != (body.answer is not None):
        raise ApiError(422, 'invalid_completion', 'Ответ не соответствует результату запуска')
    await finish(UUID(claims['run']), body.status, body.answer, body.tools_used)
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
