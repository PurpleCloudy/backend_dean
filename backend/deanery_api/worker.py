"""Separate async Procrastinate worker; bounded retries and heartbeat recovery."""
import asyncio
import csv
import hashlib
import io
import sys
import logging
from uuid import UUID, uuid5, NAMESPACE_URL

import httpx
import procrastinate
import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from psycopg.types.json import Jsonb

from . import db, storage
from .auth import load_principal
from .config import get_settings
from .errors import ApiError
from .files import get_version, get_file, principal_scope, report_hash, require_file_write
from .jobs import queue_app

app = queue_app()


class TransientJobError(Exception):
    pass


async def progress(job_id, value):
    async with db.get_pool().connection() as conn:
        await conn.execute('UPDATE backend.jobs SET progress=%s,updated_at=now() WHERE id=%s', (value, job_id))


async def fresh_principal(user_id):
    async with db.get_pool().connection() as conn:
        return await load_principal(conn, user_id)


async def set_version(principal, file_id, version_id, state, quality=None):
    principal = await fresh_principal(principal.user_id)
    async with db.transaction(principal) as conn:
        require_file_write(principal, await get_file(conn, principal, file_id))
        await conn.execute('UPDATE backend.file_versions SET state=%s,error_code=NULL,quality=COALESCE(%s,quality) WHERE id=%s',
            (state, Jsonb(quality) if quality is not None else None, version_id))


async def process_file(job, principal):
    file_id, version_id = UUID(job['payload']['file_id']), UUID(job['payload']['version_id'])
    async with db.transaction(principal) as conn:
        metadata, version = await get_version(conn, principal, file_id, version_id)
        if version['state'] == 'rejected':
            raise ApiError(422, 'file_rejected', 'Версия отклонена')
    await set_version(principal, file_id, version_id, 'scanning')
    raw = await storage.read_original(version['object_key'], version['byte_size'], version['sha256'])
    await storage.scan(raw)
    await progress(job['id'], 20)
    await set_version(principal, file_id, version_id, 'parsing')
    parsed = await storage.parse_bounded(version['filename'], raw)
    chunks = [{'id': uuid5(version_id, str(i)), 'ordinal': i, **chunk} for i, chunk in enumerate(parsed['chunks'])]
    principal = await fresh_principal(principal.user_id)
    async with db.transaction(principal) as conn:
        await get_file(conn, principal, file_id)
        # Deterministic IDs make retries replace the same immutable version's index.
        await conn.execute('DELETE FROM backend.file_chunks WHERE version_id=%s', (version_id,))
        for chunk in chunks:
            await conn.execute('INSERT INTO backend.file_chunks(id,version_id,ordinal,page,content) VALUES(%s,%s,%s,%s,%s)',
                (chunk['id'], version_id, chunk['ordinal'], chunk['page'], chunk['content']))
    await progress(job['id'], 45)
    await set_version(principal, file_id, version_id, 'indexing', parsed['quality'])
    if chunks:
        await storage.ensure_collection()
    await storage.delete_index(version_id)
    for start in range(0, len(chunks), 8):
        batch = chunks[start:start+8]
        vectors = await storage.embed([chunk['content'] for chunk in batch])
        await storage.qdrant('PUT', '/points?wait=true', json={'points': [
            {'id': str(chunk['id']), 'vector': vector, 'payload': {
                'version_id': str(version_id), 'file_id': str(file_id), 'owner_id': metadata['owner_id'],
                'institute_id': metadata['institute_id'], 'purpose': metadata['purpose'],
                'associated': metadata['association'] is not None}}
            for chunk, vector in zip(batch, vectors)]})
        await progress(job['id'], 45 + int(50 * min(start+8, len(chunks))/len(chunks)))
    principal = await fresh_principal(principal.user_id)
    async with db.transaction(principal) as conn:
        require_file_write(principal, await get_file(conn, principal, file_id, lock=True))
        await conn.execute("UPDATE backend.file_versions SET state='ready',error_code=NULL WHERE id=%s", (version_id,))
        await conn.execute('UPDATE backend.files SET active_version_id=%s WHERE id=%s AND (active_version_id IS NULL OR '
            '(SELECT version FROM backend.file_versions WHERE id=active_version_id)<=%s)', (version_id, file_id, version['version']))
    return {'file_id': str(file_id), 'version_id': str(version_id), 'chunks': len(chunks), 'quality': parsed['quality']}


def render_csv(rows):
    output = io.StringIO(newline='')
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator='\r\n')
        writer.writeheader()
        for row in rows:
            safe = {}
            for key, value in row.items():
                text = '' if value is None else str(value)
                safe[key] = "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else text
            writer.writerow(safe)
    else:
        output.write('\r\n')
    data = output.getvalue().encode('utf-8-sig')
    if len(data) > get_settings().max_upload_bytes:
        raise ApiError(413, 'export_too_large', 'Уменьшите объём экспорта')
    return data


async def export_report(job, principal):
    from .reporting import report_rows
    file_id = uuid5(job['id'], 'export-file')
    version_id = uuid5(job['id'], 'export-version')
    async with db.transaction(principal) as conn:
        old = await (await conn.execute('SELECT id FROM backend.files WHERE id=%s', (file_id,))).fetchone()
        if old:
            await get_file(conn, principal, file_id)
            return {'file_id': str(file_id), 'version_id': str(version_id)}
        data_rows = await report_rows(conn, principal, job['payload']['report'], job['payload']['filters'], job['payload']['limit'])
    scope = principal_scope(principal)
    provenance = {'report': job['payload']['report'], 'filters': job['payload']['filters'], 'limit': job['payload']['limit'],
                  'scope': scope, 'rows_hash': report_hash(data_rows)}
    rows = data_rows['items'] if isinstance(data_rows, dict) else data_rows
    raw = await asyncio.to_thread(render_csv, rows)
    await storage.scan(raw)
    key = f'originals/{file_id}/{version_id}'
    async with db.transaction(principal) as conn:
        await conn.execute('INSERT INTO backend.object_intents(object_key,version_id) VALUES(%s,%s) ON CONFLICT DO NOTHING', (key, version_id))
    await storage.put_original(key, raw, 'text/csv')
    principal = await fresh_principal(principal.user_id)
    async with db.transaction(principal) as conn:
        # Recheck report authorization before publishing the artifact.
        current_rows = await report_rows(conn, principal, job['payload']['report'], job['payload']['filters'], job['payload']['limit'])
        if principal_scope(principal) != scope or report_hash(current_rows) != provenance['rows_hash']:
            raise ApiError(409, 'export_authorization_changed', 'Права или данные изменились; создайте новый экспорт')
        await conn.execute("INSERT INTO backend.files(id,owner_id,title,purpose,active_version_id) VALUES(%s,%s,%s,'export',%s)",
            (file_id, principal.user_id, job['payload']['report'], version_id))
        await conn.execute("INSERT INTO backend.file_versions(id,file_id,version,object_key,filename,mime,byte_size,sha256,state,quality) VALUES(%s,%s,1,%s,%s,'text/csv',%s,%s,'ready',%s)",
            (version_id, file_id, key, job['payload']['report']+'.csv', len(raw), hashlib.sha256(raw).hexdigest(), Jsonb({'export': provenance})))
        await conn.execute('DELETE FROM backend.object_intents WHERE object_key=%s', (key,))
    return {'file_id': str(file_id), 'version_id': str(version_id)}


def retryable(exc):
    return (isinstance(exc, (httpx.TransportError, BotoCoreError, psycopg.OperationalError, TimeoutError))
        or isinstance(exc, ApiError) and exc.status >= 500
        or isinstance(exc, httpx.HTTPStatusError) and (exc.response.status_code >= 500 or exc.response.status_code == 429)
        or isinstance(exc, ClientError) and exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode', 0) >= 500)


@app.task(name='deanery.process_job', queue='documents', pass_context=True,
          retry=procrastinate.RetryStrategy(max_attempts=get_settings().job_max_attempts-1, exponential_wait=2,
                                           retry_exceptions=[TransientJobError]))
async def process_job(context, job_id):
    job_id = UUID(job_id)
    exhausted = False
    async with db.get_pool().connection() as conn:
        async with conn.transaction():
            job = await (await conn.execute('SELECT * FROM backend.jobs WHERE id=%s FOR UPDATE', (job_id,))).fetchone()
            if job is None or job['status'] == 'succeeded':
                return
            if job['attempts'] >= job['max_attempts']:
                await conn.execute("UPDATE backend.jobs SET status='failed',error_code='attempts_exhausted',updated_at=now() WHERE id=%s", (job_id,))
                if job['kind'] == 'process_file':
                    await conn.execute("UPDATE backend.file_versions SET state='failed',error_code='attempts_exhausted' WHERE id=%s", (UUID(job['payload']['version_id']),))
                exhausted = True
            else:
                job['attempts'] += 1
                await conn.execute("UPDATE backend.jobs SET status='running',attempts=%s,error_code=NULL,updated_at=now() WHERE id=%s", (job['attempts'], job_id))
    if exhausted:
        # Raise after committing the visible failure; queue state must also be failed.
        raise RuntimeError('attempts_exhausted')
    try:
        principal = await fresh_principal(job['owner_id'])
        async with asyncio.timeout(600):
            if job['kind'] == 'process_file':
                result = await process_file(job, principal)
            elif job['kind'] == 'export_report':
                result = await export_report(job, principal)
            elif job['kind'] == 'apply_workflow':
                from .workflows import apply_scheduled
                async with db.transaction(principal) as conn:
                    result = await apply_scheduled(conn, principal, UUID(job['payload']['event_id']))
            else:
                result = await reconcile_objects()
        async with db.get_pool().connection() as conn:
            await conn.execute("UPDATE backend.jobs SET status='succeeded',progress=100,result=%s,updated_at=now() WHERE id=%s", (Jsonb(result), job_id))
    except Exception as exc:
        can_retry = retryable(exc) and job['attempts'] < job['max_attempts']
        code = exc.code if isinstance(exc, ApiError) else ('dependency_unavailable' if can_retry else 'processing_failed')
        async with db.get_pool().connection() as conn:
            await conn.execute('UPDATE backend.jobs SET status=%s,error_code=%s,updated_at=now() WHERE id=%s',
                ('queued' if can_retry else 'failed', code, job_id))
            if job['kind'] == 'process_file':
                await conn.execute('UPDATE backend.file_versions SET state=%s,error_code=%s WHERE id=%s',
                    ('rejected' if code == 'malware_detected' else 'failed', code, UUID(job['payload']['version_id'])))
        if not can_retry and job['kind'] == 'process_file':
            try:
                await storage.delete_index(job['payload']['version_id'])
            except Exception:
                pass  # DB readiness remains authoritative until reconciliation.
        if can_retry:
            raise TransientJobError(code) from None
        # Permanent failures are recorded safely; Procrastinate must also mark failed.
        raise RuntimeError(code) from None


async def reconcile_objects():
    removed = 0
    async with db.get_pool().connection() as conn:
        rows = await (await conn.execute("SELECT object_key FROM backend.object_intents i WHERE created_at<now()-interval '15 minutes' "
            'AND NOT EXISTS(SELECT 1 FROM backend.file_versions v WHERE v.object_key=i.object_key) LIMIT 100')).fetchall()
    for row in rows:
        await storage.delete_original(row['object_key'])
        async with db.get_pool().connection() as conn:
            await conn.execute('DELETE FROM backend.object_intents WHERE object_key=%s', (row['object_key'],))
        removed += 1
    async with db.get_pool().connection() as conn:
        failed = await (await conn.execute("SELECT id FROM backend.file_versions WHERE state IN ('failed','rejected') LIMIT 100")).fetchall()
    for row in failed:
        await storage.delete_index(row['id'])
    return {'orphan_objects_removed': removed, 'failed_indexes_cleaned': len(failed)}


@app.periodic(cron='*/1 * * * *')
@app.task(name='deanery.recover', queue='maintenance')
async def recover(timestamp):
    # No age-based nb_seconds: only a dead/missing worker heartbeat can requeue.
    stalled = await app.job_manager.get_stalled_jobs(seconds_since_heartbeat=get_settings().stalled_job_timeout_seconds,
                                                    task_name='deanery.process_job')
    for pending in stalled:
        async with db.get_pool().connection() as conn:
            job = await (await conn.execute('SELECT * FROM backend.jobs WHERE id=%s', (UUID(pending.task_kwargs['job_id']),))).fetchone()
        if job and job['attempts'] < job['max_attempts']:
            await app.job_manager.retry_job(pending)
        else:
            await app.job_manager.finish_job(pending, procrastinate.jobs.Status.FAILED, delete_job=False)
            if job:
                async with db.get_pool().connection() as conn:
                    await conn.execute("UPDATE backend.jobs SET status='failed',error_code='attempts_exhausted',updated_at=now() WHERE id=%s", (job['id'],))
                    if job['kind'] == 'process_file':
                        await conn.execute("UPDATE backend.file_versions SET state='failed',error_code='attempts_exhausted' WHERE id=%s", (UUID(job['payload']['version_id']),))
    async with db.get_pool().connection() as conn:
        await conn.execute("DELETE FROM backend.delegation_nonces WHERE expires_at<now()-interval '1 hour'")
    await reconcile_objects()


async def main():
    if sys.platform == 'win32':
        raise RuntimeError('Document worker requires Linux memory limits; native Windows API uses the Linux worker')
    await db.open_pool()
    await storage.start_services()
    try:
        async with app.open_async():
            try:
                await recover.func(0)
            except (ApiError, httpx.HTTPError, BotoCoreError, ClientError):
                logging.getLogger('deanery.worker').warning('Startup reconciliation deferred: dependency unavailable')
            await app.run_worker_async(queues=['documents', 'maintenance'], concurrency=2,
                update_heartbeat_interval=10, stalled_worker_timeout=get_settings().stalled_job_timeout_seconds,
                shutdown_graceful_timeout=30)
    finally:
        await storage.stop_services()
        await db.close_pool()


if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
