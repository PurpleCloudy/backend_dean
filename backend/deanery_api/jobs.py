"""Transactional Procrastinate enqueue and user-visible job state."""
from uuid import UUID, uuid4
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field, ConfigDict
from psycopg.types.json import Jsonb
import procrastinate

from .auth import require_principal
from .config import get_settings
from .db import transaction
from .errors import ApiError
from .workflows import ScheduledWorkflowResult

router = APIRouter(tags=['jobs'])
_app = None


class ParseQuality(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ocr_performed: Literal[False]
    page_numbers: bool
    images_not_extracted: bool
    text_verified: Literal[False]
    incomplete_extraction: bool


class FileResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    file_id: UUID
    version_id: UUID


class ProcessFileResult(FileResult):
    chunks: int = Field(ge=1)
    quality: ParseQuality


class ReconcileResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    orphan_objects_removed: int = Field(ge=0)
    failed_indexes_cleaned: int = Field(ge=0)


class JobResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    kind: Literal['process_file', 'export_report', 'reconcile', 'apply_workflow']
    status: Literal['queued', 'running', 'succeeded', 'failed']
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1, le=10)
    progress: int = Field(ge=0, le=100)
    result: ProcessFileResult | FileResult | ReconcileResult | ScheduledWorkflowResult | None
    error_code: str | None


def queue_app():
    global _app
    if _app is None:
        settings = get_settings()
        _app = procrastinate.App(connector=procrastinate.PsycopgConnector(
            conninfo=settings.job_database_url or settings.database_url,
            min_size=1, max_size=5))
    return _app


async def start_services(app=None):
    await queue_app().open_async()


async def stop_services(app=None):
    if _app:
        await _app.close_async()


async def enqueue(conn, kind, payload, idempotency_key):
    if kind not in {'process_file', 'export_report', 'reconcile', 'apply_workflow'} or not 1 <= len(idempotency_key) <= 128:
        raise ApiError(422, 'invalid_job', 'Недопустимая задача')
    owner = payload['owner_id']
    row = await (await conn.execute(
        "INSERT INTO backend.jobs(id,owner_id,kind,payload,idempotency_key,max_attempts) VALUES(%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(owner_id,kind,idempotency_key) DO NOTHING RETURNING *",
        (uuid4(), owner, kind, Jsonb(payload), idempotency_key, get_settings().job_max_attempts))).fetchone()
    if row is None:
        row = await (await conn.execute('SELECT * FROM backend.jobs WHERE owner_id=%s AND kind=%s AND idempotency_key=%s',
            (owner, kind, idempotency_key))).fetchone()
        if row['payload'] != payload:
            raise ApiError(409, 'idempotency_conflict', 'Ключ уже использован для другой задачи')
        return public_job(row)
    options = {'schedule_at': datetime.fromisoformat(payload['scheduled_at'])} if payload.get('scheduled_at') else {}
    queue_id = await queue_app().configure_task(name='deanery.process_job', queue='documents',
        lock='version:' + payload['version_id'] if kind == 'process_file' else f"job:{row['id']}",
        connection=conn, **options).defer_async(job_id=str(row['id']))
    await conn.execute('UPDATE backend.jobs SET queue_id=%s WHERE id=%s', (queue_id, row['id']))
    return public_job(row)


def public_job(row):
    return {key: row[key] for key in ('id', 'kind', 'status', 'attempts', 'max_attempts', 'progress', 'result', 'error_code')}


async def owned_job(conn, principal, job_id, lock=False):
    row = await (await conn.execute('SELECT * FROM backend.jobs WHERE id=%s AND owner_id=%s' + (' FOR UPDATE' if lock else ''),
        (job_id, principal.user_id))).fetchone()
    if row is None:
        raise ApiError(404, 'job_not_found', 'Задача не найдена')
    if row['kind'] == 'process_file':
        from .files import get_file
        await get_file(conn, principal, UUID(row['payload']['file_id']))
    return row


@router.get('/jobs/{job_id}', responses={200: {'model': JobResponse}})
async def get_job(job_id: UUID, principal=Depends(require_principal)):
    async with transaction(principal) as conn:
        return public_job(await owned_job(conn, principal, job_id))


@router.post('/jobs/{job_id}/retry', status_code=202, responses={202: {'model': JobResponse}})
async def retry_job(job_id: UUID, principal=Depends(require_principal)):
    async with transaction(principal) as conn:
        job = await owned_job(conn, principal, job_id, True)
        if job['status'] != 'failed':
            raise ApiError(409, 'job_not_failed', 'Повторить можно только завершившуюся ошибкой задачу')
        # An explicit user retry opens a new bounded attempt budget, preserving history.
        return await enqueue(conn, job['kind'], job['payload'], 'retry:' + str(uuid4()))


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report: str = Field(min_length=1, max_length=80)
    filters: dict = Field(default_factory=dict)
    limit: int = Field(default=1000, ge=1, le=10000)


@router.post('/exports', status_code=202, responses={202: {'model': JobResponse}})
async def export_report(body: ExportRequest, idempotency_key: str = Header(min_length=1, max_length=128),
                        principal=Depends(require_principal)):
    from .reporting import report_rows
    async with transaction(principal) as conn:
        await report_rows(conn, principal, body.report, body.filters, 1)
        return await enqueue(conn, 'export_report', {'owner_id': principal.user_id, **body.model_dump()}, idempotency_key)
