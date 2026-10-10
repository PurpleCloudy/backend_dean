"""Trust, history and model-context limits independent of real model quality."""
import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from PIL import Image
import pytest

from integrations.dean_agent_adapter.attachments import (
    AttachmentContextError, AttachmentReader, bounded_history, private_history_content, render_visual)
from integrations.dean_agent_adapter.security import verify
from deanery_api import agent_gateway as gateway, storage
from deanery_api.errors import ApiError

SECRET = 'test-attachment-reader-secret-not-production'


def fixture_reader(handler=None, count=1, mime='text/plain', text=True):
    claims = {'user': 1, 'run': str(uuid4()), 'session': str(uuid4()), 'auth_session': str(uuid4())}
    items = [dict(file_id=str(uuid4()), version_id=str(uuid4()), ordinal=i, title='Test', source='',
        filename='x.txt', mime=mime, byte_size=4, sha256=hashlib.sha256(b'test').hexdigest(), quality=None,
        text_available=text) for i in range(count)]
    refs = [{k: item[k] for k in ('file_id','version_id')} for item in items]
    context = {'backend_run_id': claims['run'], 'canonical_request_id': 1, 'session_id': claims['session'], 'attachments': items}
    requests = []
    def respond(request):
        token = verify(SECRET, request.headers['x-delegation'], 'deanery-attachments')
        assert token['run'] == claims['run'] and token['auth_session'] == claims['auth_session']
        requests.append(token['nonce'])
        if handler:
            value = handler(request, context)
            if value is not None:
                return value
        if request.url.path.endswith('/text'):
            offset, limit = int(request.url.params['offset']), int(request.url.params['limit'])
            return httpx.Response(200, json={'version_id': request.url.path.split('/')[-2],
                'chunks': [{'ordinal': n, 'page': 1, 'text': 'Я'*1200} for n in range(offset,offset+limit)] if text else [],
                'next_offset': offset+limit if text else None, 'quality': None, 'text_available': text})
        if request.url.path.endswith('/download'):
            return httpx.Response(200, content=b'test')
        return httpx.Response(200, json=context)
    reader = AttachmentReader(httpx.Client(transport=httpx.MockTransport(respond)), 'http://backend', SECRET, claims)
    return reader, refs, context, requests


def test_five_references_initial_excerpt_is_bounded_and_continues_with_fresh_nonce():
    reader, refs, _, nonces = fixture_reader(count=5)
    try:
        prepared = reader.prepare(refs)
        assert len(prepared) == 5
        assert sum(item['text'].count('Я') for item in prepared) == 6000
        assert all(item['next_offset'] == 1 and item['initial_excerpt_only'] for item in prepared)
        for _ in range(5):
            assert reader.text(refs[0]['version_id'], 1)['chunks'][0]['ordinal'] == 1
        before = len(nonces)
        assert reader.text(refs[0]['version_id'], 2)['error'] == 'context_budget_exhausted'
        assert len(nonces) == before and len(set(nonces)) == len(nonces)
    finally:
        reader.http.close()


@pytest.mark.parametrize('case', ['wrong_run','wrong_ref','wrong_order','checksum','unbound','pagination','revoked'])
def test_trust_failures_are_not_silently_consumed(case):
    def respond(request, context):
        if case == 'wrong_run': context['backend_run_id'] = str(uuid4())
        if case == 'wrong_ref': context['attachments'][0]['file_id'] = str(uuid4())
        if case == 'wrong_order': context['attachments'].reverse()
        if case == 'checksum' and request.url.path.endswith('/download'):
            return httpx.Response(200, content=b'evil')
    reader, refs, context, requests = fixture_reader(respond, count=2, mime='text/csv' if case == 'checksum' else 'text/plain', text=case != 'checksum')
    try:
        if case in {'wrong_run','wrong_ref','wrong_order','checksum'}:
            with pytest.raises(AttachmentContextError): reader.prepare(refs)
        else:
            reader.prepare(refs)
            before = len(requests)
            if case == 'unbound':
                with pytest.raises(AttachmentContextError): reader.text(str(uuid4()))
                assert len(requests) == before
            elif case == 'pagination':
                for offset,limit in [(-1,1),(True,1),(0,3),(0,True)]:
                    with pytest.raises(AttachmentContextError): reader.text(refs[0]['version_id'],offset,limit)
                assert len(requests) == before
            else:
                reader.http.close()
                reader.http = httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(403)))
                with pytest.raises(httpx.HTTPStatusError): reader.text(refs[0]['version_id'],1)
    finally:
        reader.http.close()


def test_native_user_history_does_not_retain_prepared_text_or_metadata():
    marker = 'RAW_ONLY_73921'
    item = dict(filename='private-name.txt', version_id=str(uuid4()), byte_size=4, text=marker, blocks=[])
    content, history = private_history_content('Read the attachment', [item])
    assert marker in json.dumps(content) and history == 'Read the attachment'
    assert marker not in history and 'private-name' not in history
    next_content, next_history = private_history_content('Next question', [])
    assert next_content == next_history == 'Next question'


def test_history_trims_complete_turns_by_utf8_and_preserves_current_block_identity():
    current = {'role':'user','content':[{'type':'text','text':'current'},{'type':'image_url','image_url':{'url':'data:test'}}]}
    pair = [{'role':'user','content':'Я'*1000},{'role':'assistant','content':'Я'*1000}]
    assert bounded_history([current]) == [current]
    assert bounded_history([*pair,current],3999) == [current]
    assert bounded_history([*pair,current],4000) == [*pair,current]
    assert bounded_history([{'role':'assistant','content':'orphan'},*pair,current],4000) == [*pair,current]
    assert bounded_history([*pair,{'role':'user','content':'orphan'},current],4000) == [*pair,current]
    assert bounded_history([*pair,{'role':'user','content':'huge'*1000},{'role':'assistant','content':'huge'*1000},current]) == [current]
    assert bounded_history([*pair,current])[-1] is current


def test_attached_prompt_limit_does_not_shrink_plain_chat():
    ref = {'file_id':uuid4(),'version_id':uuid4()}
    assert len(gateway.ChatRequest(message='x'*8000).message) == 8000
    with pytest.raises(ApiError, match='2000') as error:
        gateway.ChatRequest(message='x'*2001,attachments=[ref])
    assert error.value.code == 'attachment_message_too_long'


@pytest.mark.parametrize('mime', ['image/png','application/pdf'])
async def test_visual_rejected_before_any_admission_write(monkeypatch,mime):
    from contextlib import asynccontextmanager
    principal=SimpleNamespace(user_id=1)
    conn=SimpleNamespace(execute=AsyncMock())
    @asynccontextmanager
    async def transaction(*a,**kw): yield conn
    monkeypatch.setattr(gateway,'transaction',transaction)
    monkeypatch.setattr(gateway,'load_principal',AsyncMock(return_value=principal))
    monkeypatch.setattr(gateway,'get_settings',lambda:SimpleNamespace(agent_url='http://agent',delegation_secret=SECRET))
    reader, refs, context, _ = fixture_reader(mime=mime,text=False)
    reader.http.close()
    monkeypatch.setattr(gateway,'attachment_metadata',AsyncMock(return_value=gateway.AttachmentMetadata(**context['attachments'][0])))
    with pytest.raises(ApiError) as error:
        await gateway.reserve(principal,None,'Read',[gateway.AttachmentRef(**refs[0])])
    assert error.value.code == 'agent_vision_unsupported' and error.value.status == 412
    conn.execute.assert_not_awaited()


def test_photo_full_decode_is_validated_in_parser_and_zero_text_is_explicit():
    output=io.BytesIO(); Image.new('RGB',(20,20),'red').save(output,format='PNG')
    raw=output.getvalue()
    assert storage.validate_document('x.png','image/png',raw) == 'image/png'
    value=storage.parse_document('x.png',raw)
    assert value['chunks'] == [] and value['quality']['text_available'] is False
    assert value['quality']['ocr_performed'] is False
    with pytest.raises(ApiError): storage.validate_document('x.jpg','image/jpeg',raw)
    with pytest.raises(ValueError): storage.parse_document('x.png',b'\x89PNG\r\n\x1a\ninvalid')


def test_oversized_pixels_and_animation_fail_full_parser():
    output=io.BytesIO(); Image.new('1',(5001,5000)).save(output,format='PNG')
    with pytest.raises(ValueError): storage.parse_document('x.png',output.getvalue())
    output=io.BytesIO()
    Image.new('RGB',(10,10),'red').save(output,format='PNG',save_all=True,append_images=[Image.new('RGB',(10,10),'blue')])
    with pytest.raises(ValueError): storage.parse_document('x.png',output.getvalue())


def test_visual_parser_timeout_and_output_are_failclosed(monkeypatch):
    import subprocess
    from integrations.dean_agent_adapter import attachments
    monkeypatch.setattr(attachments.sys,'platform','linux')
    monkeypatch.setattr(attachments.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=b''))
    with pytest.raises(AttachmentContextError): render_visual(b'x','image/png')
    def timeout(*a,**kw): raise subprocess.TimeoutExpired('parser',35)
    monkeypatch.setattr(attachments.subprocess,'run',timeout)
    with pytest.raises(AttachmentContextError): render_visual(b'x','image/png')
