"""Owned real worker processes: active heartbeat, hard kill and finite retries."""
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import httpx
from test_integration_support import settings, output_directory, compose_arguments


async def main():
    assert os.environ.get('RUN_LINUX_WORKER_TESTS') == '1', 'Explicit sole Linux worker/provider lease required'
    expected_image = os.environ['VERIFIED_WORKER_IMAGE_ID']
    assert expected_image.startswith('sha256:') and len(expected_image) == 71
    root = Path(__file__).resolve().parents[1]
    env = settings()
    env['DATABASE_URL'] = env['TEST_DATABASE_URL']
    env['JOB_DATABASE_URL'] = env['TEST_JOB_DATABASE_URL']
    env['STALLED_JOB_TIMEOUT_SECONDS'] = '15'
    os.environ.update(env)
    from deanery_api import db, jobs
    from deanery_api.auth import load_principal
    from test_integration_files_http import await_job
    await db.open_pool()
    await jobs.start_services()
    processes = []
    proof = uuid4().hex
    output = output_directory(env)
    artifact = output/('linux-worker-recovery-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    evidence = {'image': expected_image, 'proof': proof, 'containers': [], 'cases': []}

    def docker(*args):
        result = subprocess.run(['docker', *args], cwd=root, capture_output=True, text=True, timeout=45)
        if result.returncode: raise AssertionError('Docker operation failed: '+args[0]+'; exit '+str(result.returncode))
        return (result.stdout+result.stderr if args[0]=='logs' else result.stdout).strip()

    class OwnedWorker:
        def __init__(self):
            self.name='deanery-recovery-'+proof[:10]+'-'+str(len(processes)+1)
            self.id=docker(*compose_arguments(env),'run','--no-deps','-d','--name',self.name,
                '--label','deanery.proof='+proof,'--label','deanery.owner=integration-recovery',
                '-e','STALLED_JOB_TIMEOUT_SECONDS=15','-e','BGE_URL=http://providers:8231','worker')
            processes.append(self)
            # Inspect only safe immutable identity/labels, never container env.
            self.identity=json.loads(docker('inspect','--format','{{json .Image}}',self.id))
            labels=json.loads(docker('inspect','--format','{{json .Config.Labels}}',self.id))
            assert labels.get('deanery.proof')==proof and labels.get('deanery.owner')=='integration-recovery'
            assert self.identity==expected_image, 'Worker image differs from frozen verifier image'
            docker('update','--restart=no',self.id)
            policy=json.loads(docker('inspect','--format','{{json .HostConfig.RestartPolicy}}',self.id))
            assert policy['Name']=='no'
            evidence['containers'].append({'id':self.id,'name':self.name,'image':self.identity,'restart_policy':policy,'ownership':{'proof':proof,'owner':'integration-recovery'}})

        def poll(self):
            state=json.loads(docker('inspect','--format','{{json .State}}',self.id))
            return None if state['Running'] else state['ExitCode']

        def kill(self):docker('kill',self.id)

        def wait(self,timeout=10):return int(docker('wait',self.id))

    def record(case,expected,actual):
        evidence['cases'].append({'id':case,'expected':expected,'actual':actual,'status':'pass'})

    def start():
        process = OwnedWorker()
        print('START owned Linux worker', process.name, flush=True)
        return process

    try:
        async with httpx.AsyncClient(base_url=env.get('BASE_URL','http://127.0.0.1:8000'), trust_env=False, timeout=30,
                                     headers={'Origin': 'http://localhost:5173'}) as client, \
                   httpx.AsyncClient(base_url=env.get('TEST_PROVIDER_URL','http://127.0.0.1:58231'), trust_env=False, timeout=10) as provider:
            login = await client.post('/api/v1/auth/login', json={'login': 'admin', 'password': env['BOOTSTRAP_PASSWORD']})
            assert login.status_code == 200, login.text
            client.headers['Authorization'] = 'Bearer '+login.json()['access_token']
            async with db.get_pool().connection() as conn:
                user = await (await conn.execute("SELECT user_id FROM backend.login_record('admin')")).fetchone()
                principal = await load_principal(conn, user['user_id'])
            async with db.transaction(principal) as conn:
                async with conn.transaction(force_rollback=True):
                    rolled_back = await jobs.enqueue(conn,'reconcile',{'owner_id':principal.user_id},'rollback-'+proof)
                absent = await (await conn.execute("SELECT (SELECT count(*) FROM backend.jobs WHERE id=%s) AS backend_rows,(SELECT count(*) FROM public.procrastinate_jobs WHERE args->>'job_id'=%s) AS queue_rows",(rolled_back['id'],str(rolled_back['id'])))).fetchone()
            assert dict(absent)=={'backend_rows':0,'queue_rows':0}
            record('LINUX-TRANSACTIONAL-ENQUEUE-ROLLBACK',{'backend_rows':0,'queue_rows':0},{'job_id':rolled_back['id'],**dict(absent)})
            await provider.post('/control', json={'delay_seconds': 45, 'bge_status': 200})
            original = start()
            upload = await client.post('/api/v1/files', files={'file': ('recovery.txt', b'Recovery verification durable original.', 'text/plain')},
                data={'title': 'Recovery verification'}, headers={'Idempotency-Key': 'kill-'+str(uuid4())})
            assert upload.status_code == 202, upload.text
            data = upload.json()
            for _ in range(100):
                progress = (await client.get('/api/v1/jobs/'+data['job']['id'])).json()
                if progress['progress'] >= 45:
                    break
                assert original.poll() is None, 'Owned worker exited; inspect its log'
                await asyncio.sleep(.2)
            assert progress['progress'] >= 45, progress
            await asyncio.sleep(20)
            stalled = await jobs.queue_app().job_manager.get_stalled_jobs(seconds_since_heartbeat=15, task_name='deanery.process_job')
            assert not any(p.task_kwargs.get('job_id') == data['job']['id'] for p in stalled)
            progress = (await client.get('/api/v1/jobs/'+data['job']['id'])).json()
            assert progress['attempts'] == 1 and progress['status'] == 'running', progress
            record('LINUX-ACTIVE-HEARTBEAT-NOT-RECOVERED',{'attempts':1,'status':'running','not_stalled':True},{'upload':data,'job':progress,'stalled_job_ids':[p.task_kwargs.get('job_id') for p in stalled],'container':original.id})
            print('PASS active job older than stale threshold is not recovered; heartbeat remains live', flush=True)
            original.kill()
            await asyncio.to_thread(original.wait, timeout=10)
            assert original.poll()==137,'SIGKILL must stop the owned Linux worker'
            record('LINUX-WORKER-HARDKILL',{'exit_code':137,'restart':'no'},{'container':original.id,'exit_code':original.poll()})
            print('KILL owned Linux worker', original.name, flush=True)
            await provider.post('/control', json={'delay_seconds': 0})
            await asyncio.sleep(17)
            start()
            recovered = await await_job(client, data['job']['id'])
            assert recovered['status'] == 'succeeded' and recovered['attempts'] == 2, recovered
            async with db.get_pool().connection() as conn:
                counts = await (await conn.execute('SELECT count(*) AS n,count(DISTINCT ordinal) AS distinct_n FROM backend.file_chunks WHERE version_id=%s', (data['version_id'],))).fetchone()
                assert counts['n'] == counts['distinct_n'] == 1
                state = await (await conn.execute('SELECT status::text AS status FROM public.procrastinate_jobs WHERE args->>\'job_id\'=%s', (data['job']['id'],))).fetchone()
                assert state['status'] == 'succeeded', state
            record('LINUX-STALE-RECOVERY-ONE-VERSION',{'attempts':2,'status':'succeeded','chunks':1,'queue_status':'succeeded'},{'upload':data,'job':recovered,'chunks':dict(counts),'queue':dict(state)})
            print('PASS hard-kill stale heartbeat recovery: attempts2, same original/version/chunk, both states succeeded', flush=True)
            await provider.post('/control', json={'bge_status': 503})
            upload = await client.post('/api/v1/files', files={'file': ('retry.txt', b'Retry verification original.', 'text/plain')},
                data={'title': 'Retry verification'}, headers={'Idempotency-Key': 'retry-'+str(uuid4())})
            assert upload.status_code == 202, upload.text
            retry = upload.json()
            failed = await await_job(client, retry['job']['id'])
            assert failed['status'] == 'failed' and failed['attempts'] == failed['max_attempts'] == 3, failed
            await asyncio.sleep(.5)
            async with db.get_pool().connection() as conn:
                state = await (await conn.execute('SELECT q.status::text AS status FROM public.procrastinate_jobs q JOIN backend.jobs j ON j.queue_id=q.id WHERE j.id=%s', (retry['job']['id'],))).fetchone()
                assert state['status'] == 'failed', state
            record('LINUX-RETRY-EXHAUSTION',{'attempts':3,'backend':'failed','queue':'failed'},{'upload':retry,'job':failed,'queue':dict(state)})
            print('PASS finite classified retries exhausted: backend failed and Procrastinate failed, attempts3', flush=True)
            await provider.post('/control', json={'bge_status': 200})
            response = await client.post('/api/v1/jobs/'+retry['job']['id']+'/retry')
            assert response.status_code == 202, response.text
            result = await await_job(client, response.json()['id'])
            assert result['status'] == 'succeeded'
            async with db.get_pool().connection() as conn:
                counts = await (await conn.execute('SELECT count(*) AS n,count(DISTINCT ordinal) AS distinct_n FROM backend.file_chunks WHERE version_id=%s', (retry['version_id'],))).fetchone()
                assert counts['n'] == counts['distinct_n'] == 1
                user = await (await conn.execute("SELECT user_id FROM backend.login_record('admin')")).fetchone()
                principal = await load_principal(conn, user['user_id'])
            record('LINUX-EXPLICIT-RETRY-IDEMPOTENT',{'status':'succeeded','chunks':1},{'job':result,'chunks':dict(counts),'version_id':retry['version_id']})
            print('PASS explicit retry after dependency recovery: same version, no duplicate chunks', flush=True)
            async with db.transaction(principal) as conn:
                exhausted = await jobs.enqueue(conn, 'reconcile', {'owner_id': principal.user_id}, 'exhausted-'+str(uuid4()))
                await conn.execute('UPDATE backend.jobs SET attempts=max_attempts WHERE id=%s', (exhausted['id'],))
            guarded = await await_job(client, exhausted['id'])
            await asyncio.sleep(.5)
            async with db.get_pool().connection() as conn:
                state = await (await conn.execute('SELECT q.status::text AS status FROM public.procrastinate_jobs q JOIN backend.jobs j ON j.queue_id=q.id WHERE j.id=%s', (exhausted['id'],))).fetchone()
            assert guarded['status'] == 'failed' and state['status'] == 'failed'
            record('LINUX-ALREADY-EXHAUSTED-GUARD',{'backend':'failed','queue':'failed'},{'job':guarded,'queue':dict(state)})
            print('PASS already-exhausted job guard truthfully fails both state stores', flush=True)
    except BaseException as error:
        evidence['failure']={'type':type(error).__name__,'message':str(error)[:1500]}
        raise
    finally:
        cleanup_errors=[]
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as provider:
                response=await provider.post(env.get('TEST_PROVIDER_URL','http://127.0.0.1:58231')+'/control', json={'delay_seconds': 0, 'bge_status': 200})
                response.raise_for_status()
        except Exception as error:cleanup_errors.append({'step':'restore fixture provider','error':type(error).__name__})
        for process in processes:
            try:
                if process.poll() is None:
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=10)
                (output/(process.name+'.log')).write_text(docker('logs',process.id),encoding='utf8')
                docker('rm',process.id)
            except Exception as error:cleanup_errors.append({'container':process.id,'error':type(error).__name__})
        evidence['cleanup_errors']=cleanup_errors
        artifact.write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str),encoding='utf8')
        print('Evidence',artifact,flush=True)
        await jobs.stop_services()
        await db.close_pool()
        assert not cleanup_errors,cleanup_errors


if __name__ == '__main__':
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None)
