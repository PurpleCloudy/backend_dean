"""Opt-in real HTTP attachment checks and a deliberately compatible protocol peer.

The peer is a test fixture, not attachment understanding in the original agent.
Run with RUN_CHAT_ATTACHMENT_TESTS=1 and an exclusive local synthetic DB lease.
Stages: snapshot-before, snapshot-after, original, peer. The lifecycle owner
switches AGENT_URL to this module's --serve-peer between original/peer stages.
One current evidence file is written; secrets and delegation tokens are omitted.
"""
import argparse
import asyncio
import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
from argon2 import PasswordHasher
from psycopg.rows import dict_row

from integrations.dean_agent_adapter.security import sign, verify
from test_integration_files_http import await_job
from test_integration_support import settings, output_directory

META = {'file_id', 'version_id', 'ordinal', 'title', 'source', 'filename', 'mime',
        'byte_size', 'sha256', 'quality', 'text_available'}
ORIGIN = {'Origin': 'http://localhost:5173'}
TEXT = ('Immutable attachment verification. Кириллица.\n' * 100).encode()
EICAR = b'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def peer_app():
    """Real localhost HTTP peer: validates signing, claims and reads actual storage."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response
    app = FastAPI()
    secret = os.environ['DELEGATION_SECRET']
    backend = os.environ.get('BACKEND_URL', 'http://api:8000')
    state = {'health': 'true', 'calls': [], 'release': asyncio.Event()}

    @app.get('/health')
    async def health():
        return {'status': 'ready'}

    @app.get('/health/scoped')
    async def scoped(request: Request):
        try:
            verify(secret, request.headers.get('X-Delegation', ''), 'deanery-health')
        except ValueError:
            return JSONResponse({'detail': 'signature required'}, 401)
        mode = state['health']
        if mode == 'timeout':
            await asyncio.sleep(3)
        if mode == 'malformed':
            return {'status': 'ready', 'adapter': 'scoped-v1', 'capabilities': {'attachments_context_v1': 'true'}}
        value = {'status': 'ready', 'adapter': 'scoped-v1'}
        if mode != 'legacy':
            value['capabilities'] = {'attachments_context_v1': mode != 'false'}
        return value

    @app.post('/control')
    async def control(request: Request):
        value = await request.json()
        if 'health' in value:
            state['health'] = value['health']
        if value.get('release'):
            state['release'].set()
        if value.get('reset'):
            state['release'] = asyncio.Event()
        return {'ok': True}

    @app.get('/trace')
    async def trace():
        return {'calls': state['calls']}

    @app.post('/chat')
    @app.post('/chat/stream')
    async def chat(request: Request):
        raw = await request.body()
        try:
            token = request.headers.get('X-Delegation', '')
            claims = verify(secret, token, 'dean-agent')
            payload = json.loads(raw)
            assert sha(raw) == claims['body_sha256']
            assert request.headers['X-Actor-ID'] == f"user:{claims['user']}"
            assert payload['session_id'] == claims['session']
        except (ValueError, KeyError, AssertionError):
            return JSONResponse({'detail': 'bad signed request'}, 401)
        async with httpx.AsyncClient(base_url=backend, trust_env=False, timeout=20) as client:
            claim = await client.post('/api/v1/internal/agent/claim', headers={'X-Delegation': token})
            if claim.status_code != 200:
                return JSONResponse({'detail': 'claim rejected'}, 401)
            entry = {'claims': {key: claims[key] for key in ('user', 'session', 'run', 'auth_session')},
                     'payload': payload, 'claim_status': 200, 'reads': []}
            state['calls'].append(entry)
            if payload['message'] == 'fixture:hold':
                await asyncio.wait_for(state['release'].wait(), 45)
            if payload['message'] == 'fixture:fail':
                return JSONResponse({'detail': 'controlled peer failure'}, 503)
            headers = lambda: {'X-Delegation': sign(secret, claims, 'deanery-attachments')}
            manifest = await client.get('/api/v1/internal/agent/attachments', headers=headers())
            entry['manifest'] = {'status': manifest.status_code, 'body': manifest.json()}
            if manifest.status_code == 200:
                for item in manifest.json()['attachments']:
                    path = '/api/v1/internal/agent/attachments/' + item['version_id']
                    text = await client.get(path + '/text?limit=1', headers=headers())
                    download = await client.get(path + '/download', headers=headers())
                    entry['reads'].append({'version_id': item['version_id'], 'text': {'status': text.status_code, 'body': text.json()},
                        'download': {'status': download.status_code, 'bytes': len(download.content), 'sha256': sha(download.content)}})
            answer = {'run_id': str(uuid4()), 'session_id': payload['session_id'],
                      'answer': 'Controlled protocol peer; no language-model attachment understanding claimed.',
                      'proposals': [], 'tools_used': []}
            if request.url.path.endswith('/stream'):
                frames = [('session', {'session_id': payload['session_id']}), ('done', answer)]
                return Response(''.join('event: '+name+'\ndata: '+json.dumps(value)+'\n\n' for name, value in frames), media_type='text/event-stream')
            return answer
    return app


class Check:
    def __init__(self, env):
        self.env = env
        self.path = output_directory(env) / 'chat-attachments.json'
        self.proof = json.loads(self.path.read_text('utf8')) if self.path.exists() else {'cases': []}
        self.db = psycopg.connect(env['TEST_MIGRATION_DATABASE_URL'], autocommit=True, row_factory=dict_row)
        address = urlsplit(env['TEST_MIGRATION_DATABASE_URL'])
        assert address.hostname in {'127.0.0.1', 'localhost'} and address.port == 55433
        assert address.path == '/deanery_app', 'Only the explicitly leased local synthetic app database'
        self.proof['database'] = {'host': address.hostname, 'port': address.port, 'name': 'deanery_app'}
        self.proof['limitations'] = ['Supported attachment flow uses a compatible HTTP protocol peer, not original-agent understanding.',
            'Total-size boundary uses temporary byte_size values on owned ready test versions; storage bytes are verified separately.']

    def save(self):
        self.proof['updated_at'] = datetime.now(timezone.utc).isoformat()
        self.path.write_text(json.dumps(self.proof, ensure_ascii=False, indent=2, default=str), 'utf8')

    def record(self, name, expected, actual, checks):
        row = {'id': name, 'expected': expected, 'actual': actual, 'checks': checks,
               'status': 'pass' if all(checks.values()) else 'fail'}
        self.proof['cases'] = [item for item in self.proof['cases'] if item['id'] != name] + [row]
        self.save()
        assert all(checks.values()), name
        print('PASS', name, flush=True)

    def counters(self):
        tables = ['backend.agent_runs', 'deanery.agent_request', 'backend.agent_run_attachments', 'public.chat_sessions', 'public.chat_messages']
        return {table: self.db.execute('SELECT count(*) AS n FROM '+table).fetchone()['n'] for table in tables}

    async def login(self, client, login='admin', password=None):
        result = await client.post('/api/v1/auth/login', json={'login': login, 'password': password or self.env['BOOTSTRAP_PASSWORD']}, headers=ORIGIN)
        assert result.status_code == 200, (result.status_code, result.text)
        return {**ORIGIN, 'Authorization': 'Bearer '+result.json()['access_token']}

    async def upload(self, client, raw=TEXT, filename='attachment.txt'):
        result = await client.post('/api/v1/files', files={'file': (filename, raw, 'text/plain')},
            data={'title': 'Chat attachment verification', 'purpose': 'attachment'}, headers={'Idempotency-Key': 'chat-attachment-'+str(uuid4())})
        assert result.status_code == 202, result.text
        value = result.json()
        job = await await_job(client, value['job']['id'])
        assert job['status'] == ('failed' if raw == EICAR else 'succeeded'), job
        return {key: value[key] for key in ('file_id', 'version_id')}

    async def no_admission(self, client, name, payload, status, code=None):
        before = self.counters()
        response = await client.post('/api/v1/agent/chat', json=payload)
        after = self.counters()
        self.record(name, {'status': status, 'code': code, 'admission_unchanged': True},
            {'status': response.status_code, 'body': response.json(), 'before': before, 'after': after},
            {'status': response.status_code == status, 'code': code is None or response.json().get('error', {}).get('code') == code,
             'no_admission': before == after})

    def snapshot(self, stage):
        tables = ['deanery.student', 'deanery.grade', 'deanery.agent_request', 'public.chat_sessions', 'public.chat_messages',
                  'backend.agent_runs', 'backend.files', 'backend.file_versions', 'backend.file_chunks']
        value = {table: self.db.execute("SELECT count(*) AS count,md5(COALESCE(string_agg(v, E'\\n' ORDER BY v),'')) AS digest FROM (SELECT row_to_json(t)::text AS v FROM "+table+' t) x').fetchone() for table in tables}
        head = self.db.execute('SELECT version_num FROM public.alembic_version').fetchone()['version_num']
        if stage == 'snapshot-before':
            self.proof['migration_before'] = {'head': head, 'tables': value}
            self.save()
            print('Saved bounded existing-data snapshot', flush=True)
        else:
            before = self.proof['migration_before']
            self.record('additive-upgrade-preserves-existing-data', {'head': '0003', 'same_existing_rows': True},
                {'before': before, 'after': {'head': head, 'tables': value}},
                {'head': before['head'] == '0002' and head == '0003', 'preserved': value == before['tables']})

    async def original(self):
        async with httpx.AsyncClient(base_url=self.env.get('BASE_URL', 'http://127.0.0.1:8000'), trust_env=False, timeout=60) as client:
            client.headers.update(await self.login(client))
            response = await client.get('/api/v1/agent/capabilities')
            expected = {'protocol': 'v1', 'backend_supported': True, 'agent_supported': False, 'agent_status': 'ready',
                        'max_attachments': 5, 'max_total_bytes': 20000000, 'max_text_chunks': 50}
            self.record('installed-agent-explicit-unsupported', expected, {'status': response.status_code, 'body': response.json()},
                        {'exact_contract': response.status_code == 200 and response.json() == {'attachment_context': expected}})
            refs = [{'file_id': str(uuid4()), 'version_id': str(uuid4())}]
            await self.no_admission(client, 'unsupported-before-admission', {'message': 'Read this', 'attachments': refs}, 412, 'agent_attachments_unsupported')
            session = str(uuid4())
            raw = json.dumps({'message': 'Read this', 'session_id': session, 'attachments': refs}).encode()
            claims = {'user': 1, 'session': session, 'run': str(uuid4()), 'body_sha256': sha(raw)}
            token = sign(self.env['DELEGATION_SECRET'], claims, 'dean-agent')
            before = self.counters()
            async with httpx.AsyncClient(trust_env=False, timeout=10) as upstream:
                direct = await upstream.post(self.env.get('TEST_AGENT_URL', 'http://127.0.0.1:58001')+'/chat', content=raw,
                    headers={'Content-Type': 'application/json', 'X-Delegation': token, 'X-Actor-ID': 'user:1'})
            self.record('adapter-never-silently-discards-attachments', {'status': 412, 'admission_unchanged': True},
                {'status': direct.status_code, 'body': direct.json()}, {'denied': direct.status_code == 412, 'unchanged': before == self.counters()})

    async def peer(self):
        async with httpx.AsyncClient(base_url=self.env.get('BASE_URL', 'http://127.0.0.1:8000'), trust_env=False, timeout=60) as client, \
                   httpx.AsyncClient(base_url=self.env.get('TEST_ATTACHMENT_PEER_URL', 'http://127.0.0.1:58011'), trust_env=False, timeout=10) as peer:
            client.headers.update(await self.login(client))
            await peer.post('/control', json={'health': 'true'})
            refs = [await self.upload(client), await self.upload(client, b'Second immutable attachment.')]
            first = refs[0]
            response = await client.post(f"/api/v1/files/{first['file_id']}/versions", files={'file': ('attachment.txt', b'New active version.', 'text/plain')},
                headers={'Idempotency-Key': 'chat-new-version-'+str(uuid4())})
            assert response.status_code == 202 and (await await_job(client, response.json()['job']['id']))['status'] == 'succeeded'
            new_version = response.json()['version_id']
            for mode, status in [('false', 412), ('legacy', 412), ('malformed', 503), ('timeout', 503)]:
                await peer.post('/control', json={'health': mode})
                calls = len((await peer.get('/trace')).json()['calls'])
                await self.no_admission(client, 'capability-'+mode, {'message': 'Read these', 'attachments': refs}, status)
                assert len((await peer.get('/trace')).json()['calls']) == calls
            await peer.post('/control', json={'health': 'true'})
            cap = await client.get('/api/v1/agent/capabilities')
            assert cap.json()['attachment_context']['agent_supported'] is True
            result = await client.post('/api/v1/agent/chat', json={'message': 'Read these', 'attachments': refs})
            assert result.status_code == 200, result.text
            value = result.json(); context = value['attachment_context']
            run = self.db.execute('SELECT id,session_id,user_id,canonical_request_id,status FROM backend.agent_runs WHERE id=%s', (context['backend_run_id'],)).fetchone()
            request = self.db.execute('SELECT request_text,status FROM deanery.agent_request WHERE agent_request_id=%s', (context['canonical_request_id'],)).fetchone()
            links = self.db.execute('SELECT file_id,version_id,ordinal FROM backend.agent_run_attachments WHERE run_id=%s ORDER BY ordinal', (run['id'],)).fetchall()
            trace = (await peer.get('/trace')).json()['calls'][-1]
            self.record('ready-old-version-json-canonical-link', {'request_text': 'Read these', 'versions': refs, 'exact_bytes': [sha(TEXT), sha(b'Second immutable attachment.')]},
                {'response': value, 'run': run, 'request': request, 'links': links, 'peer': trace, 'new_active_version': new_version},
                {'exact_refs': [(str(x['file_id']),str(x['version_id']),x['ordinal']) for x in links] == [(x['file_id'],x['version_id'],i) for i,x in enumerate(refs)],
                 'canonical_link': run['canonical_request_id'] == context['canonical_request_id'] and request['request_text'] == 'Read these' and run['status'] == 'done',
                 'metadata_whitelist': all(set(x) == META for x in context['attachments']),
                 'immutable_downloads': [x['download']['sha256'] for x in trace['reads']] == [sha(TEXT), sha(b'Second immutable attachment.')],
                 'bounded_text': all(x['text']['status'] == 200 and len(x['text']['body']['chunks']) == 1 and len(x['text']['body']['chunks'][0]['text']) <= 1200 for x in trace['reads']),
                 'separate_run_ids': value['run_id'] != context['backend_run_id']})
            owner = await client.get('/api/v1/agent/runs/'+context['backend_run_id']+'/attachments')
            assert owner.status_code == 200 and owner.json() == context
            stream = await client.post('/api/v1/agent/chat/stream', json={'message': 'Stream these', 'session_id': value['session_id'], 'attachments': refs})
            events = [(frame.splitlines()[0][7:], json.loads(frame.splitlines()[1][6:])) for frame in stream.text.strip().split('\n\n')]
            self.record('sse-context-and-session-continuation', {'events': ['session', 'done'], 'same_context': True}, {'status': stream.status_code, 'events': events},
                {'status': stream.status_code == 200, 'events': [x[0] for x in events] == ['session','done'],
                 'context': events[0][1]['attachment_context'] == events[1][1]['attachment_context'],
                 'session': events[1][1]['session_id'] == value['session_id']})
            for name, payload, status, code in [
                ('duplicate-version', {'message':'Bad','attachments':[first,first]},422,'duplicate_attachment'),
                ('too-many', {'message':'Bad','attachments':[{'file_id':str(uuid4()),'version_id':str(uuid4())} for _ in range(6)]},422,None),
                ('malformed-ref', {'message':'Bad','attachments':[{'file_id':'invalid','version_id':str(uuid4())}]},422,None),
                ('mixed-valid-missing', {'message':'Bad','attachments':[first,{'file_id':str(uuid4()),'version_id':str(uuid4())}]},404,'file_not_found'),
                ('wrong-file-version-pair', {'message':'Bad','attachments':[{'file_id':first['file_id'],'version_id':refs[1]['version_id']}]},404,'version_not_found'),
                ('foreign-session', {'message':'Bad','session_id':str(uuid4()),'attachments':refs},404,'session_not_found')]:
                await self.no_admission(client,name,payload,status,code)
            infected = await self.upload(client, EICAR, 'eicar.txt')
            await self.no_admission(client,'rejected-virus',{'message':'Bad','attachments':[first,infected]},409,'file_not_ready')
            self.db.execute("UPDATE backend.file_versions SET state='indexing' WHERE id=%s",(first['version_id'],))
            try:
                await self.no_admission(client,'not-yet-ready',{'message':'Bad','attachments':[first]},409,'file_not_ready')
            finally:
                self.db.execute("UPDATE backend.file_versions SET state='ready' WHERE id=%s",(first['version_id'],))
            boundary_refs = [*refs, {'file_id': first['file_id'], 'version_id': new_version}]
            sizes = self.db.execute('SELECT id,byte_size FROM backend.file_versions WHERE id=ANY(%s::uuid[])',([x['version_id'] for x in boundary_refs],)).fetchall()
            try:
                for item in refs:
                    self.db.execute('UPDATE backend.file_versions SET byte_size=10000000 WHERE id=%s',(item['version_id'],))
                # Deliberate metadata-only admission boundary. Do not download a
                # synthetic Content-Length; the held peer is released after restore.
                await peer.post('/control',json={'reset':True})
                task = asyncio.create_task(client.post('/api/v1/agent/chat',json={'message':'fixture:hold','attachments':refs}))
                await self.wait_call(peer)
                self.db.execute('UPDATE backend.file_versions SET byte_size=9999999 WHERE id=%s',(refs[1]['version_id'],))
                self.db.execute('UPDATE backend.file_versions SET byte_size=2 WHERE id=%s',(new_version,))
                await self.no_admission(client,'aggregate-limit-plus-one',{'message':'Too large','attachments':boundary_refs},413,'attachments_too_large')
            finally:
                for item in sizes:
                    self.db.execute('UPDATE backend.file_versions SET byte_size=%s WHERE id=%s',(item['byte_size'],item['id']))
                await peer.post('/control',json={'release':True})
            boundary = await task
            self.record('aggregate-limit-exact',{'admitted_total':20000000,'fixture':'temporary metadata only'}, {'status':boundary.status_code,'metadata_sizes':[x['byte_size'] for x in boundary.json()['attachment_context']['attachments']]}, {'accepted':boundary.status_code==200})
            export = await client.post('/api/v1/exports',json={'report':'students','limit':3},headers={'Idempotency-Key':'chat-export-'+str(uuid4())})
            assert export.status_code == 202, export.text
            job = await await_job(client,export.json()['id']); assert job['status']=='succeeded',job
            exported = {key:job['result'][key] for key in ('file_id','version_id')}
            csv = await client.post('/api/v1/agent/chat',json={'message':'Read export','attachments':[exported]})
            assert csv.status_code==200,csv.text
            csv_trace=(await peer.get('/trace')).json()['calls'][-1]
            txt=csv_trace['reads'][0]['text']['body']
            self.record('csv-binary-without-extracted-text',{'text_available':False,'chunks':[],'next_offset':None,'download':200},csv_trace,
                {'metadata':csv.json()['attachment_context']['attachments'][0]['text_available'] is False,
                 'empty_text':txt['text_available'] is False and txt['chunks']==[] and txt['next_offset'] is None,
                 'private_download':csv_trace['reads'][0]['download']['status']==200})
            await self.security(client,peer,refs,context)
            failed_before=self.counters()
            failed=await client.post('/api/v1/agent/chat',json={'message':'fixture:fail','attachments':refs})
            trace=(await peer.get('/trace')).json()['calls'][-1]
            run=self.db.execute('SELECT status,canonical_request_id FROM backend.agent_runs WHERE id=%s',(trace['claims']['run'],)).fetchone()
            n=self.db.execute('SELECT count(*) AS n FROM backend.agent_run_attachments WHERE run_id=%s',(trace['claims']['run'],)).fetchone()['n']
            recovery=await client.get('/api/v1/agent/runs/'+trace['claims']['run']+'/attachments')
            self.record('peer-failure-keeps-one-failed-canonical-run',{'status':503,'run':'failed','links':2,'recoverable_context':True}, {'status':failed.status_code,'body':failed.json(),'run':run,'links':n,'before':failed_before,'after':self.counters(),'recovered_context':recovery.json()},
                {'status':failed.status_code==503,'failed_run':run['status']=='failed','exact_links':n==2,
                 'recoverable_context':failed.json()['error']['details']['run_id']==trace['claims']['run'] and recovery.status_code==200 and len(recovery.json()['attachments'])==2})
            attempts=[]
            for role,statement,params in [
                ('deanery_runtime','DELETE FROM backend.agent_run_attachments WHERE run_id=%s',(trace['claims']['run'],)),
                ('deanery_jobs','UPDATE backend.file_versions SET sha256=%s WHERE id=%s',('0'*64,first['version_id'])),
                ('deanery_runtime','UPDATE backend.file_versions SET byte_size=1 WHERE id=%s',(first['version_id'],)),
                ('deanery_reader','SELECT * FROM backend.agent_run_attachments',()),
                ('deanery_upstream','SELECT * FROM backend.agent_run_attachments',())]:
                observed=None
                try:
                    with self.db.transaction(force_rollback=True):
                        self.db.execute('SET LOCAL ROLE '+role)
                        self.db.execute(statement,params)
                except psycopg.Error as exc:
                    observed=exc.sqlstate
                attempts.append({'role':role,'sql':statement,'sqlstate':observed})
            self.record('native-bindings-and-original-identity-immutable',{'sqlstate':'42501'},attempts,
                {'all_denied':all(x['sqlstate']=='42501' for x in attempts)})

    async def wait_call(self, peer):
        for _ in range(100):
            calls=(await peer.get('/trace')).json()['calls']
            if calls and calls[-1]['payload']['message']=='fixture:hold' and self.db.execute('SELECT status FROM backend.agent_runs WHERE id=%s',(calls[-1]['claims']['run'],)).fetchone()['status']=='running':
                return calls[-1]
            await asyncio.sleep(.05)
        raise AssertionError('Peer did not claim held run')

    async def security(self, client, peer, refs, admin_context):
        actor=self.db.execute("SELECT u.user_id,u.login,u.password_hash,u.is_active FROM deanery.app_user u JOIN deanery.app_role r USING(role_id) WHERE r.code='student' AND u.is_active ORDER BY u.user_id LIMIT 1").fetchone()
        assert actor,'Supplied synthetic student required'
        password=secrets.token_urlsafe(24)
        self.db.execute('UPDATE deanery.app_user SET password_hash=%s WHERE user_id=%s',(PasswordHasher().hash(password),actor['user_id']))
        tasks=[]
        try:
            async with httpx.AsyncClient(base_url=str(client.base_url),trust_env=False,timeout=60) as student:
                student.headers.update(await self.login(student,actor['login'],password))
                await self.no_admission(student,'foreign-file',{'message':'Bad','attachments':[refs[0]]},404,'file_not_found')
                denied=await student.get('/api/v1/agent/runs/'+admin_context['backend_run_id']+'/attachments')
                self.record('public-run-owner-only',{'status':404},{'status':denied.status_code,'body':denied.json()},{'hidden':denied.status_code==404})
                own=await self.upload(student,b'Private student attachment.')
                await self.no_admission(student,'actual-foreign-session',{'message':'Bad','session_id':admin_context['session_id'],'attachments':[own]},404,'session_not_found')
                await peer.post('/control',json={'reset':True})
                task=asyncio.create_task(student.post('/api/v1/agent/chat',json={'message':'fixture:hold','attachments':[own]}));tasks.append(task)
                trace=await self.wait_call(peer);claims=trace['claims']
                await self.no_admission(student,'same-session-concurrency',{'message':'Concurrent','session_id':claims['session'],'attachments':[own]},409,'session_busy')
                async def probe(path,aud='deanery-attachments',changes=None,token=None):
                    return await client.get('/api/v1/internal/agent/attachments'+path,
                        headers={'X-Delegation': token if token is not None else sign(self.env['DELEGATION_SECRET'],{**claims,**(changes or {})},aud)})
                for name,path,aud,changes,status in [
                    ('wrong-audience','','deanery-tools',None,401),
                    ('wrong-run','','deanery-attachments',{'run':str(uuid4())},401),
                    ('wrong-session','','deanery-attachments',{'session':str(uuid4())},401),
                    ('unbound-version','/'+refs[0]['version_id']+'/download','deanery-attachments',None,404),
                    ('text-limit','/'+own['version_id']+'/text?limit=51','deanery-attachments',None,422)]:
                    response=await probe(path,aud,changes)
                    self.record('delegation-'+name,{'status':status},{'status':response.status_code,'body':response.json()},{'denied':response.status_code==status})
                unsigned=await probe('',token='')
                token=sign(self.env['DELEGATION_SECRET'],claims,'deanery-attachments')
                good=await probe('',token=token);replay=await probe('',token=token)
                self.record('signed-manifest-and-replay',{'unsigned':401,'valid':200,'replay':401},{'statuses':[unsigned.status_code,good.status_code,replay.status_code],'manifest':good.json()},
                    {'statuses':[unsigned.status_code,good.status_code,replay.status_code]==[401,200,401]})
                self.db.execute('UPDATE backend.files SET owner_id=(SELECT user_id FROM deanery.app_user WHERE login=%s) WHERE id=%s',('admin',own['file_id']))
                try:
                    lost=await probe('/'+own['version_id']+'/text')
                    public=await student.get('/api/v1/agent/runs/'+claims['run']+'/attachments')
                    self.record('current-file-acl-after-bind',{'internal':404,'public':404},{'statuses':[lost.status_code,public.status_code]},
                        {'denied':[lost.status_code,public.status_code]==[404,404]})
                finally:
                    self.db.execute('UPDATE backend.files SET owner_id=%s WHERE id=%s',(actor['user_id'],own['file_id']))
                self.db.execute('UPDATE deanery.app_user SET is_active=false WHERE user_id=%s',(actor['user_id'],))
                try:
                    inactive=await probe('')
                    self.record('current-inactive-principal',{'status':401},{'status':inactive.status_code,'body':inactive.json()},{'denied':inactive.status_code==401})
                finally:
                    self.db.execute('UPDATE deanery.app_user SET is_active=true WHERE user_id=%s',(actor['user_id'],))
                self.db.execute('UPDATE backend.sessions SET revoked_at=now() WHERE id=%s',(claims['auth_session'],))
                revoked=await probe('')
                self.record('current-revoked-auth-session',{'status':401},{'status':revoked.status_code,'body':revoked.json()},{'denied':revoked.status_code==401})
                await peer.post('/control',json={'release':True})
                done=await task
                assert done.status_code==200,done.text
        finally:
            await peer.post('/control',json={'release':True})
            if tasks:
                await asyncio.gather(*tasks,return_exceptions=True)
            self.db.execute('UPDATE deanery.app_user SET password_hash=%s,is_active=%s WHERE user_id=%s',(actor['password_hash'],actor['is_active'],actor['user_id']))


async def main(stage):
    env=settings()
    assert env.get('RUN_CHAT_ATTACHMENT_TESTS')=='1','Explicit local synthetic DB and peer lifecycle lease required'
    check=Check(env)
    try:
        if stage.startswith('snapshot-'):
            check.snapshot(stage)
        elif stage=='original':
            await check.original()
        elif stage=='peer':
            await check.peer()
    finally:
        check.save();check.db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve-peer',action='store_true')
    parser.add_argument('--stage',choices=['snapshot-before','snapshot-after','original','peer'])
    args=parser.parse_args()
    if args.serve_peer:
        import uvicorn
        uvicorn.run(peer_app(),host='0.0.0.0',port=8001)
    else:
        assert args.stage,'Choose an explicit stage'
        asyncio.run(main(args.stage))
