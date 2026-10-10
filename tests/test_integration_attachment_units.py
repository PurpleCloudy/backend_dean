"""Small attachment contract checks; the separate HTTP suite exercises real services."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

os.environ.setdefault('DATABASE_URL', 'postgresql://test:test@127.0.0.1/test')
os.environ.setdefault('ACCESS_TOKEN_KEY', 'attachment-unit-test-key-not-production')
os.environ.setdefault('DEVELOPMENT', 'true')

from deanery_api import agent_gateway as gateway
from deanery_api.auth import Principal
from deanery_api.errors import ApiError

PRINCIPAL = Principal(1, 'test', frozenset({'admin'}), None, frozenset(), None, None)
SETTINGS = SimpleNamespace(agent_url='http://agent', delegation_secret='attachment-unit-delegation-secret-only',
                           agent_timeout_seconds=120, agent_max_concurrency=4)


def metadata(reference, size=10, text=True):
    return gateway.AttachmentMetadata(**reference.model_dump(), ordinal=0, title='Document', source='urn:test',
        filename='document.txt', mime='text/plain', byte_size=size, sha256='a'*64, quality=None, text_available=text)


def transaction_with(monkeypatch, conn):
    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield conn
    monkeypatch.setattr(gateway, 'transaction', transaction)
    monkeypatch.setattr(gateway, 'load_principal', AsyncMock(return_value=PRINCIPAL))


def test_attachment_input_is_only_bounded_unique_refs():
    reference = {'file_id': str(uuid4()), 'version_id': str(uuid4())}
    assert gateway.ChatRequest(message='Hello').attachments == []
    assert len(gateway.ChatRequest(message='Hello', attachments=[reference]).attachments) == 1
    with pytest.raises(ApiError) as error:
        gateway.ChatRequest(message='Hello', attachments=[reference, reference])
    assert error.value.code == 'duplicate_attachment'
    for value in ([{**reference, 'url': 'http://untrusted'}],
                  [{'file_id': str(uuid4()), 'version_id': str(uuid4())} for _ in range(6)]):
        with pytest.raises(ValidationError):
            gateway.ChatRequest(message='Hello', attachments=value)


@pytest.mark.parametrize('body,expected', [
    ({'status': 'ready', 'adapter': 'scoped-v1'}, False),
    ({'status': 'ready', 'adapter': 'scoped-v1', 'capabilities': {'attachments_context_v1': False}}, False),
    ({'status': 'ready', 'adapter': 'scoped-v1', 'capabilities': {'attachments_context_v1': True}}, True),
    ({'status': 'ready', 'adapter': 'scoped-v1', 'capabilities': {'attachments_context_v1': 'true'}}, 'error'),
    ({'status': 'ready', 'adapter': 'scoped-v1', 'capabilities': None}, 'error'),
    ({'status': 'ready', 'adapter': 'raw-original'}, 'error'),
])
def test_signed_capability_contract(monkeypatch, body, expected):
    async def check():
        def handle(request):
            assert request.url.path == '/health/scoped' and request.headers.get('x-delegation')
            return httpx.Response(200, json=body)
        monkeypatch.setattr(gateway, 'get_settings', lambda: SETTINGS)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            monkeypatch.setattr(gateway, '_http', client)
            if expected == 'error':
                with pytest.raises(ApiError) as error:
                    await gateway.adapter_capabilities()
                assert error.value.status == 503 and error.value.code == 'agent_unavailable'
            else:
                assert (await gateway.adapter_capabilities()).attachments_context_v1 is expected
    asyncio.run(check())


@pytest.mark.parametrize('failure', ['oversized', 'unavailable'])
def test_capability_response_is_bounded_and_failclosed(monkeypatch, failure):
    async def check():
        def handle(request):
            if failure == 'unavailable':
                raise httpx.ConnectError('unavailable', request=request)
            return httpx.Response(200, content=b' ' * 4097)
        monkeypatch.setattr(gateway, 'get_settings', lambda: SETTINGS)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            monkeypatch.setattr(gateway, '_http', client)
            with pytest.raises(ApiError) as error:
                await gateway.adapter_capabilities()
            assert error.value.status == 503
    asyncio.run(check())


@pytest.mark.parametrize('stream', [False, True])
def test_unsupported_agent_rejects_before_admission(monkeypatch, stream):
    monkeypatch.setattr(gateway, 'adapter_capabilities', AsyncMock(return_value=gateway.AdapterCapabilities(attachments_context_v1=False)))
    reserve = AsyncMock()
    monkeypatch.setattr(gateway, 'reserve', reserve)
    body = gateway.ChatRequest(message='Document', attachments=[{'file_id': uuid4(), 'version_id': uuid4()}])
    with pytest.raises(ApiError) as error:
        asyncio.run(gateway.upstream_request(None, body, PRINCIPAL, stream))
    assert error.value.status == 412 and error.value.code == 'agent_attachments_unsupported'
    reserve.assert_not_awaited()


@pytest.mark.parametrize('failure', ['last_acl', 'total_bytes'])
def test_all_references_validated_before_durable_admission(monkeypatch, failure):
    references = [gateway.AttachmentRef(file_id=uuid4(), version_id=uuid4()) for _ in range(3)]
    conn = SimpleNamespace(execute=AsyncMock())
    transaction_with(monkeypatch, conn)
    monkeypatch.setattr(gateway, 'get_settings', lambda: SETTINGS)
    values = [metadata(references[0], 10_000_000), metadata(references[1], 9_999_999),
              ApiError(404, 'file_not_found', 'Unavailable') if failure == 'last_acl' else metadata(references[2], 2)]
    monkeypatch.setattr(gateway, 'attachment_metadata', AsyncMock(side_effect=values))
    with pytest.raises(ApiError) as error:
        asyncio.run(gateway.reserve(PRINCIPAL, None, 'Document', references))
    assert error.value.status == (404 if failure == 'last_acl' else 413)
    conn.execute.assert_not_awaited()


def test_metadata_pins_requested_version_and_hides_storage_and_export_scope(monkeypatch):
    reference = gateway.AttachmentRef(file_id=uuid4(), version_id=uuid4())
    version = dict(filename='export.csv', mime='text/csv', byte_size=50, sha256='b'*64,
                   object_key='private/key', quality={'export': {'scope': {'secret': 'not public'}}})
    get_version = AsyncMock(return_value=({'title': 'Report', 'source': '', 'active_version_id': uuid4()}, version))
    monkeypatch.setattr(gateway, 'get_version', get_version)
    cursor = SimpleNamespace(fetchone=AsyncMock(return_value={'value': False}))
    conn = SimpleNamespace(execute=AsyncMock(return_value=cursor))
    result = asyncio.run(gateway.attachment_metadata(conn, PRINCIPAL, reference, 0))
    get_version.assert_awaited_once_with(conn, PRINCIPAL, reference.file_id, reference.version_id, ready=True)
    assert result.version_id == reference.version_id and result.quality is None and not result.text_available
    assert all(value not in result.model_dump_json() for value in ('private/key', 'scope', 'secret', 'active_version'))


def test_foreign_or_unbound_version_never_reaches_file_reader():
    run_id, version_id = uuid4(), uuid4()
    cursor = SimpleNamespace(fetchone=AsyncMock(return_value=None))
    conn = SimpleNamespace(execute=AsyncMock(return_value=cursor))
    with pytest.raises(ApiError) as error:
        asyncio.run(gateway.bound_attachment(conn, PRINCIPAL, run_id, version_id))
    assert error.value.status == 404
    assert conn.execute.await_args.args[1] == (run_id, version_id, PRINCIPAL.user_id)


@pytest.mark.parametrize('has_text', [True, False])
def test_delegated_text_pages_existing_chunks_and_reports_unavailable(monkeypatch, has_text):
    run_id = uuid4()
    reference = gateway.AttachmentRef(file_id=uuid4(), version_id=uuid4())
    claims = {'run': str(run_id)}
    delegated = AsyncMock(return_value=(claims, PRINCIPAL))
    monkeypatch.setattr(gateway, 'delegated', delegated)
    rows = [{'ordinal': i, 'page': 1, 'text': 'x'*1200} for i in range(51)] if has_text else []
    cursor = SimpleNamespace(fetchall=AsyncMock(return_value=rows))
    conn = SimpleNamespace(execute=AsyncMock(return_value=cursor))
    transaction_with(monkeypatch, conn)
    bound = AsyncMock(return_value=(reference, 0))
    monkeypatch.setattr(gateway, 'bound_attachment', bound)
    monkeypatch.setattr(gateway, 'attachment_metadata', AsyncMock(return_value=metadata(reference, text=has_text)))
    request = object()
    result = asyncio.run(gateway.delegated_attachment_text(reference.version_id, request, offset=0, limit=50))
    delegated.assert_awaited_once_with(request, 'deanery-attachments')
    bound.assert_awaited_once_with(conn, PRINCIPAL, run_id, reference.version_id)
    assert len(result.chunks) == (50 if has_text else 0)
    assert result.next_offset == (50 if has_text else None) and result.text_available is has_text


def test_signed_download_reuses_the_existing_acl_and_private_stream(monkeypatch):
    reference = gateway.AttachmentRef(file_id=uuid4(), version_id=uuid4())
    request, response, run_id = object(), object(), uuid4()
    delegated = AsyncMock(return_value=({'run': str(run_id)}, PRINCIPAL))
    monkeypatch.setattr(gateway, 'delegated', delegated)
    transaction_with(monkeypatch, object())
    monkeypatch.setattr(gateway, 'bound_attachment', AsyncMock(return_value=(reference, 0)))
    download = AsyncMock(return_value=response)
    monkeypatch.setattr(gateway, 'download_file', download)
    assert asyncio.run(gateway.delegated_attachment_download(reference.version_id, request)) is response
    delegated.assert_awaited_once_with(request, 'deanery-attachments')
    download.assert_awaited_once_with(reference.file_id, reference.version_id, PRINCIPAL)


@pytest.mark.parametrize('attached', [False, True])
def test_chat_preserves_original_wire_and_adds_separate_backend_handle(monkeypatch, attached):
    backend_id, upstream_id, session_id = uuid4(), uuid4(), uuid4()
    original = {'run_id': str(upstream_id), 'session_id': str(session_id), 'answer': 'Answer', 'proposals': [], 'tools_used': []}
    context = {'backend_run_id': str(backend_id), 'canonical_request_id': 7, 'session_id': str(session_id), 'attachments': []} if attached else None
    async def chunks():
        yield json.dumps(original).encode()
    response = SimpleNamespace(aiter_bytes=chunks, aclose=AsyncMock())
    monkeypatch.setattr(gateway, 'upstream_request', AsyncMock(return_value=(backend_id, session_id, response, context)))
    monkeypatch.setattr(gateway, 'finish', AsyncMock())
    monkeypatch.setattr(gateway, 'get_settings', lambda: SETTINGS)
    result = asyncio.run(gateway.chat(gateway.ChatRequest(message='Hello'), None, PRINCIPAL))
    assert result['run_id'] == str(upstream_id)
    if attached:
        assert result['attachment_context']['backend_run_id'] == str(backend_id)
    else:
        assert result == original and len(result) == 5


def test_attached_http_error_exposes_admitted_backend_handle(monkeypatch):
    async def check():
        backend_id, session_id = uuid4(), uuid4()
        monkeypatch.setattr(gateway, 'get_settings', lambda: SETTINGS)
        monkeypatch.setattr(gateway, 'adapter_capabilities', AsyncMock(return_value=gateway.AdapterCapabilities(attachments_context_v1=True)))
        monkeypatch.setattr(gateway, 'reserve', AsyncMock(return_value=(backend_id, session_id, {'backend_run_id': str(backend_id)})))
        monkeypatch.setattr(gateway, 'finish', AsyncMock())
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
            monkeypatch.setattr(gateway, '_http', client)
            body = gateway.ChatRequest(message='Read', attachments=[{'file_id': uuid4(), 'version_id': uuid4()}])
            with pytest.raises(ApiError) as error:
                await gateway.upstream_request(SimpleNamespace(state=SimpleNamespace(session_id=uuid4())), body, PRINCIPAL, False)
            assert error.value.details == {'run_id': str(backend_id), 'session_id': str(session_id)}
    asyncio.run(check())


@pytest.mark.parametrize('content_type', ['application/json', 'text/event-stream'])
def test_attached_stream_errors_expose_admitted_backend_handle(monkeypatch, content_type):
    async def check():
        backend_id, session_id = uuid4(), uuid4()
        async def chunks():
            yield b'event: error\ndata: {"message":"private upstream diagnostic"}\n\n'
        response = SimpleNamespace(headers={'content-type': content_type}, aiter_bytes=chunks, aclose=AsyncMock())
        monkeypatch.setattr(gateway, 'upstream_request', AsyncMock(return_value=(backend_id, session_id, response, {'backend_run_id': str(backend_id)})))
        monkeypatch.setattr(gateway, 'finish', AsyncMock())
        monkeypatch.setattr(gateway, 'get_settings', lambda: SETTINGS)
        if content_type != 'text/event-stream':
            with pytest.raises(ApiError) as error:
                await gateway.chat_stream(gateway.ChatRequest(message='Read'), None, PRINCIPAL)
            value = error.value.details
        else:
            stream = await gateway.chat_stream(gateway.ChatRequest(message='Read'), None, PRINCIPAL)
            frames = [frame async for frame in stream.body_iterator]
            value = json.loads(frames[0].split('data: ', 1)[1])
            assert 'private upstream diagnostic' not in frames[0]
        assert value['run_id'] == str(backend_id) and value['session_id'] == str(session_id)
    asyncio.run(check())
