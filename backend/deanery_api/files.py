"""Immutable private originals, typed associations and current-principal ACLs."""
import asyncio
import hashlib
import json
from datetime import datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from psycopg.types.json import Jsonb

from .auth import require_principal
from .config import get_settings
from .db import transaction
from .errors import ApiError
from . import storage, jobs
from .schemas import json_value

router = APIRouter(tags=['files'])


class Association(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resource: str = Field(min_length=1, max_length=80)
    key: dict | int | str


class AssociationResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resource: str
    key: dict[str, int | str] | int | str


class ExportScope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: int
    roles: list[str]
    institute_ids: list[int]
    person_id: int | None
    teacher_id: int | None
    student_id: int | None


class ExportProvenance(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report: str
    filters: dict[str, str | int | float | bool | None]
    limit: int
    scope: ExportScope
    rows_hash: str = Field(pattern='^[0-9a-f]{64}$')


class ExportQuality(BaseModel):
    model_config = ConfigDict(extra='forbid')
    export: ExportProvenance


class PendingQuality(BaseModel):
    """No extraction metadata exists before parsing succeeds."""
    model_config = ConfigDict(extra='forbid')


class FileVersionResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    version: int = Field(ge=1)
    filename: str
    mime: Literal['text/plain', 'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'text/csv']
    byte_size: int = Field(ge=1)
    sha256: str = Field(pattern='^[0-9a-f]{64}$')
    state: Literal['quarantined', 'scanning', 'parsing', 'indexing', 'ready', 'rejected', 'failed']
    quality: jobs.ParseQuality | ExportQuality | PendingQuality
    error_code: str | None
    created_at: datetime


class FileResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    owner_id: int
    institute_id: int | None
    association: AssociationResponse | None
    title: str
    source: str
    purpose: Literal['attachment', 'regulation', 'export']
    active_version_id: UUID | None
    created_at: datetime
    versions: list[FileVersionResponse]


class UploadResponse(jobs.FileResult):
    job: jobs.JobResponse


async def check_association(conn, principal, association):
    if association:
        from .resources import read_resource
        value = await read_resource(conn, principal, association['resource'], association['key'], limit=1)
        if not value['items']:
            raise ApiError(404, 'association_not_found', 'Связанная запись не найдена')


async def get_file(conn, principal, file_id, lock=False):
    row = await (await conn.execute('SELECT * FROM backend.files WHERE id=%s' + (' FOR UPDATE' if lock else ''), (file_id,))).fetchone()
    if row is None:
        raise ApiError(404, 'file_not_found', 'Файл не найден')
    staff_admin = 'admin' in principal.roles
    in_scope = row['institute_id'] is not None and row['institute_id'] in principal.institute_ids
    if row['institute_id'] is not None and not (staff_admin or in_scope):
        raise ApiError(404, 'file_not_found', 'Файл не найден')
    published = row['purpose'] == 'regulation' and (in_scope or row['institute_id'] is None)
    if row['owner_id'] != principal.user_id and not (staff_admin or row['association'] or published):
        raise ApiError(404, 'file_not_found', 'Файл не найден')
    await check_association(conn, principal, row['association'])
    if row['purpose'] == 'export':
        from .reporting import report_rows
        version = await (await conn.execute('SELECT quality FROM backend.file_versions WHERE id=%s', (row['active_version_id'],))).fetchone()
        provenance = version['quality'].get('export') if version else None
        if not provenance or provenance['scope'] != principal_scope(principal):
            raise ApiError(403, 'export_authorization_changed', 'Права изменились; создайте новый экспорт')
        current = await report_rows(conn, principal, provenance['report'], provenance['filters'], provenance['limit'])
        if report_hash(current) != provenance['rows_hash']:
            raise ApiError(403, 'export_authorization_changed', 'Исходные данные изменились; создайте новый экспорт')
    return row


def principal_scope(principal):
    return {'user_id': principal.user_id, 'roles': sorted(principal.roles), 'institute_ids': sorted(principal.institute_ids),
            'person_id': principal.person_id, 'teacher_id': principal.teacher_id, 'student_id': principal.student_id}


def report_hash(rows):
    return hashlib.sha256(json.dumps(json_value(rows), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def require_file_write(principal, row):
    if row['purpose'] == 'export':
        raise ApiError(409, 'protected_export', 'Создайте новый экспорт отчёта')
    if row['purpose'] == 'regulation':
        if row['institute_id'] is None and 'admin' not in principal.roles:
            raise ApiError(403, 'forbidden', 'Общие положения публикует администратор')
        if not principal.roles & {'admin', 'dean_staff', 'director'}:
            raise ApiError(403, 'forbidden', 'Нормативные документы публикует деканат')
    elif row['owner_id'] != principal.user_id and not principal.roles & {'admin', 'dean_staff', 'director'}:
        raise ApiError(403, 'forbidden', 'Недостаточно прав для изменения файла')


async def get_version(conn, principal, file_id, version_id, ready=False):
    file_row = await get_file(conn, principal, file_id)
    version = await (await conn.execute('SELECT * FROM backend.file_versions WHERE id=%s AND file_id=%s', (version_id, file_id))).fetchone()
    if version is None:
        raise ApiError(404, 'version_not_found', 'Версия не найдена')
    if ready and version['state'] != 'ready':
        raise ApiError(409, 'file_not_ready', 'Версия ещё не прошла обработку')
    return file_row, version


async def upload_bytes(upload):
    data = bytearray()
    try:
        while chunk := await upload.read(65536):
            data.extend(chunk)
            if len(data) > get_settings().max_upload_bytes:
                raise ApiError(413, 'file_too_large', 'Превышен максимальный размер файла')
    finally:
        await upload.close()
    data = bytes(data)
    mime = await asyncio.to_thread(storage.validate_document, upload.filename, upload.content_type or '', data)
    return data, mime


async def save_original(principal, data, filename, mime, title, source, purpose, association,
                        institute_id, idempotency_key, file_id=None):
    checksum = hashlib.sha256(data).hexdigest()
    request_hash = report_hash({'sha256': checksum, 'filename': filename, 'mime': mime, 'title': title,
        'source': source, 'purpose': purpose, 'association': association, 'institute_id': institute_id,
        'file_id': str(file_id) if file_id else None})
    version_id = uuid4()
    new_file_id = file_id or uuid4()
    object_key = f'originals/{new_file_id}/{version_id}'
    # Durable intent is committed before object I/O, so a crash can be reconciled.
    async with transaction(principal) as conn:
        if file_id:
            owned = await get_file(conn, principal, file_id)
            require_file_write(principal, owned)
        elif institute_id is not None and 'admin' not in principal.roles and institute_id not in principal.institute_ids:
            raise ApiError(403, 'forbidden', 'Институт вне области доступа')
        await check_association(conn, principal, association)
        old = await (await conn.execute("SELECT * FROM backend.jobs WHERE owner_id=%s AND kind='process_file' AND idempotency_key=%s",
            (principal.user_id, idempotency_key))).fetchone()
        if old:
            if old['payload'].get('request_hash') != request_hash:
                raise ApiError(409, 'idempotency_conflict', 'Ключ уже использован для другого файла')
            return {'file_id': old['payload']['file_id'], 'version_id': old['payload']['version_id'], 'job': jobs.public_job(old)}
        await conn.execute('INSERT INTO backend.object_intents(object_key,version_id) VALUES(%s,%s)', (object_key, version_id))
    try:
        await storage.put_original(object_key, data, mime)
        async with transaction(principal) as conn:
            await conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (f'file-upload:{principal.user_id}:{idempotency_key}',))
            old = await (await conn.execute("SELECT * FROM backend.jobs WHERE owner_id=%s AND kind='process_file' AND idempotency_key=%s",
                (principal.user_id, idempotency_key))).fetchone()
            if old:
                if old['payload'].get('request_hash') != request_hash:
                    raise ApiError(409, 'idempotency_conflict', 'Ключ уже использован для другого файла')
                await storage.delete_original(object_key)
                await conn.execute('DELETE FROM backend.object_intents WHERE object_key=%s', (object_key,))
                return {'file_id': old['payload']['file_id'], 'version_id': old['payload']['version_id'], 'job': jobs.public_job(old)}
            if file_id:
                require_file_write(principal, await get_file(conn, principal, file_id, lock=True))
                version = (await (await conn.execute('SELECT COALESCE(max(version),0)+1 AS n FROM backend.file_versions WHERE file_id=%s', (file_id,))).fetchone())['n']
            else:
                await check_association(conn, principal, association)
                await conn.execute('INSERT INTO backend.files(id,owner_id,institute_id,association,title,source,purpose) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                    (new_file_id, principal.user_id, institute_id, Jsonb(association) if association else None, title, source, purpose))
                version = 1
            await conn.execute('INSERT INTO backend.file_versions(id,file_id,version,object_key,filename,mime,byte_size,sha256,state) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,\'quarantined\')',
                (version_id, new_file_id, version, object_key, filename, mime, len(data), checksum))
            job = await jobs.enqueue(conn, 'process_file', {'owner_id': principal.user_id, 'file_id': str(new_file_id),
                'version_id': str(version_id), 'sha256': checksum, 'request_hash': request_hash}, idempotency_key)
            await conn.execute('DELETE FROM backend.object_intents WHERE object_key=%s', (object_key,))
        return {'file_id': new_file_id, 'version_id': version_id, 'job': job}
    except BaseException:
        # Cleanup is best effort; the committed intent covers crash/storage failure.
        try:
            await asyncio.shield(storage.delete_original(object_key))
        except Exception:
            pass
        raise


@router.post('/files', status_code=202, responses={202: {'model': UploadResponse}})
async def upload_file(file: UploadFile = File(), title: str = Form(min_length=1, max_length=255),
                      source: str = Form(default='', max_length=512), purpose: str = Form(default='attachment'),
                      association: str | None = Form(default=None), institute_id: int | None = Form(default=None),
                      idempotency_key: str = Header(min_length=1, max_length=128), principal=Depends(require_principal)):
    if not title.strip():
        raise ApiError(422, 'invalid_title', 'Укажите название документа')
    if purpose not in {'attachment', 'regulation'}:
        raise ApiError(422, 'invalid_purpose', 'Недопустимый тип документа')
    if purpose == 'regulation' and not principal.roles & {'admin', 'director', 'dean_staff'}:
        raise ApiError(403, 'forbidden', 'Нормативные документы публикует деканат')
    if purpose == 'regulation' and institute_id is None and 'admin' not in principal.roles:
        raise ApiError(403, 'institute_required', 'Для положения института укажите свой институт')
    try:
        link = Association.model_validate_json(association).model_dump() if association else None
    except ValidationError as exc:
        raise ApiError(422, 'invalid_association', 'Неверная связь документа') from exc
    data, mime = await upload_bytes(file)
    return await save_original(principal, data, file.filename, mime, title, source, purpose, link, institute_id, idempotency_key)


@router.post('/files/{file_id}/versions', status_code=202, responses={202: {'model': UploadResponse}})
async def upload_version(file_id: UUID, file: UploadFile = File(), idempotency_key: str = Header(min_length=1, max_length=128),
                         principal=Depends(require_principal)):
    async with transaction(principal) as conn:
        metadata = await get_file(conn, principal, file_id)
        if metadata['purpose'] == 'export':
            raise ApiError(409, 'protected_export', 'Создайте новый экспорт отчёта')
    data, mime = await upload_bytes(file)
    return await save_original(principal, data, file.filename, mime, metadata['title'], metadata['source'],
        metadata['purpose'], metadata['association'], metadata['institute_id'], idempotency_key, file_id)


@router.get('/files/{file_id}', responses={200: {'model': FileResponse}})
async def file_status(file_id: UUID, principal=Depends(require_principal)):
    async with transaction(principal) as conn:
        metadata = await get_file(conn, principal, file_id)
        versions = await (await conn.execute('SELECT id,version,filename,mime,byte_size,sha256,state,quality,error_code,created_at FROM backend.file_versions WHERE file_id=%s ORDER BY version', (file_id,))).fetchall()
        return {**metadata, 'versions': versions}


@router.get('/files/{file_id}/versions/{version_id}/download', response_class=StreamingResponse,
    responses={200: {'description': 'Private original bytes of a ready immutable version.',
        'content': {mime: {'schema': {'type': 'string', 'format': 'binary'}} for mime in
            ('text/plain', 'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'text/csv')},
        'headers': {'Content-Disposition': {'schema': {'type': 'string'}}, 'Content-Length': {'schema': {'type': 'integer'}}, 'ETag': {'schema': {'type': 'string'}}}}})
async def download_file(file_id: UUID, version_id: UUID, principal=Depends(require_principal)):
    async with transaction(principal) as conn:
        _, version = await get_version(conn, principal, file_id, version_id, ready=True)
    response = await storage.open_original(version['object_key'])
    return StreamingResponse(storage.original_chunks(version['object_key'], response), media_type=version['mime'], headers={
        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(version['filename'], safe=''),
        'Content-Length': str(version['byte_size']), 'ETag': '"'+version['sha256']+'"',
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@router.post('/files/{file_id}/versions/{version_id}/reindex', status_code=202, responses={202: {'model': jobs.JobResponse}})
async def reindex_file(file_id: UUID, version_id: UUID, idempotency_key: str = Header(min_length=1, max_length=128),
                       principal=Depends(require_principal)):
    async with transaction(principal) as conn:
        metadata, version = await get_version(conn, principal, file_id, version_id)
        require_file_write(principal, metadata)
        if version['state'] == 'rejected':
            raise ApiError(409, 'file_rejected', 'Отклонённая версия не обрабатывается повторно')
        return await jobs.enqueue(conn, 'process_file', {'owner_id': principal.user_id, 'file_id': str(file_id),
            'version_id': str(version_id), 'sha256': version['sha256']}, idempotency_key)
