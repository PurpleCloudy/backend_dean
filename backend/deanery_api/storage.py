"""Private object storage, scanner and bounded document/vector processing."""
import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import re
from pathlib import PurePosixPath
import sys
import subprocess
from urllib.parse import quote
import zipfile
from xml.etree import ElementTree

import httpx
from aiobotocore.session import get_session
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .config import get_settings
from .errors import ApiError

_stack = None
_s3 = None
_http = None
MIMES = {'.pdf': 'application/pdf', '.txt': 'text/plain',
         '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         '.csv': 'text/csv'}
MAX_TEXT = 2_000_000


async def start_services(app=None):
    global _stack, _s3, _http
    settings = get_settings()
    _stack = AsyncExitStack()
    await _stack.__aenter__()
    _http = await _stack.enter_async_context(httpx.AsyncClient(
        trust_env=False, timeout=httpx.Timeout(120, connect=5),
        limits=httpx.Limits(max_connections=12, max_keepalive_connections=6)))
    if settings.s3_endpoint_url and settings.s3_access_key and settings.s3_secret_key:
        _s3 = await _stack.enter_async_context(get_session().create_client(
            's3', endpoint_url=settings.s3_endpoint_url, region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key, aws_secret_access_key=settings.s3_secret_key,
            config=Config(connect_timeout=5, read_timeout=30,
                          retries={'max_attempts': 0}, s3={'addressing_style': 'path'})))


async def stop_services(app=None):
    global _stack, _s3, _http
    if _stack:
        await _stack.aclose()
    _stack = _s3 = _http = None


def client():
    if _s3 is None:
        raise ApiError(503, 'storage_unavailable', 'Хранилище не настроено')
    return _s3


async def put_original(key, data, mime):
    try:
        await client().put_object(Bucket=get_settings().s3_bucket, Key=key, Body=data,
                                 ContentType=mime, Metadata={'sha256': hashlib.sha256(data).hexdigest()})
    except (BotoCoreError, ClientError) as exc:
        raise ApiError(503, 'storage_unavailable', 'Хранилище временно недоступно') from exc


async def delete_original(key):
    await client().delete_object(Bucket=get_settings().s3_bucket, Key=key)


async def open_original(key):
    try:
        return await client().get_object(Bucket=get_settings().s3_bucket, Key=key)
    except (BotoCoreError, ClientError) as exc:
        raise ApiError(503, 'storage_unavailable', 'Оригинал временно недоступен') from exc


async def original_chunks(key, response=None):
    response = response if response is not None else await open_original(key)
    async with response['Body'] as body:
        while data := await body.read(65536):
            yield data


async def read_original(key, expected_size, expected_sha256):
    result = bytearray()
    async for chunk in original_chunks(key):
        result.extend(chunk)
        if len(result) > min(get_settings().max_upload_bytes, expected_size):
            raise ApiError(422, 'object_size_mismatch', 'Размер оригинала изменился')
    data = bytes(result)
    if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ApiError(422, 'object_checksum_mismatch', 'Контрольная сумма оригинала изменилась')
    return data


def validate_document(filename, mime, data, allow_csv=False):
    if not filename or len(filename) > 255 or any(c in filename for c in '/\\') or '..' in filename or any(ord(c) < 32 for c in filename):
        raise ApiError(422, 'invalid_filename', 'Недопустимое имя файла')
    extension = PurePosixPath(filename).suffix.lower()
    allowed = set(MIMES) if allow_csv else set(MIMES) - {'.csv'}
    if extension not in allowed or mime.split(';')[0].strip().lower() != MIMES[extension]:
        raise ApiError(415, 'unsupported_type', 'Разрешены PDF, DOCX и UTF-8 TXT с соответствующим MIME')
    if not data:
        raise ApiError(422, 'empty_file', 'Файл пуст')
    if extension == '.pdf':
        if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-2048:]:
            raise ApiError(422, 'invalid_pdf', 'Неверная сигнатура PDF')
    elif extension == '.docx':
        try:
            if not data.startswith(b'PK\x03\x04'):
                raise ValueError('ZIP signature')
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                if len(entries) > 2048 or len(names) != len(set(names)):
                    raise ValueError('ZIP entries')
                if not {'[Content_Types].xml', 'word/document.xml'} <= set(names):
                    raise ValueError('DOCX parts')
                if any('..' in PurePosixPath(n).parts or n.startswith('/') or '\\' in n for n in names):
                    raise ValueError('ZIP path')
                if sum(e.file_size for e in entries) > 40_000_000:
                    raise ValueError('ZIP expanded size')
                if any(e.file_size > 20_000_000 or e.flag_bits & 1 or e.file_size > max(e.compress_size, 1)*200 for e in entries):
                    raise ValueError('ZIP ratio, encryption or member size')
                if any(n.lower().endswith('vbaproject.bin') or n.startswith('word/embeddings/') for n in names):
                    raise ValueError('active content')
        except (zipfile.BadZipFile, ValueError, OSError) as exc:
            raise ApiError(422, 'invalid_docx', 'Повреждённый или небезопасный DOCX') from exc
    else:
        try:
            text = data.decode('utf-8-sig')
            if '\x00' in text or any(ord(c) < 32 and c not in '\r\n\t' for c in text):
                raise ValueError('binary text')
        except (UnicodeError, ValueError) as exc:
            raise ApiError(422, 'invalid_text', 'Ожидался UTF-8 текст') from exc
    return MIMES[extension]


async def scanner_command(command, data=None):
    """Bounded clamd command channel shared by version checks and scanning."""
    settings = get_settings()
    try:
        async with asyncio.timeout(45 if data is not None else 2):
            reader, writer = await asyncio.open_connection(settings.clamav_host, settings.clamav_port, limit=4096)
            try:
                writer.write(b'z' + command + b'\0')
                if data is not None:
                    for start in range(0, len(data), 65536):
                        chunk = data[start:start+65536]
                        writer.write(len(chunk).to_bytes(4, 'big') + chunk)
                        await writer.drain()
                    writer.write(b'\0\0\0\0')
                await writer.drain()
                response = await reader.readuntil(b'\0')
                if len(response) > 4096:
                    raise ValueError('invalid scanner response')
            finally:
                writer.close()
                await writer.wait_closed()
    except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
        raise ApiError(503, 'scanner_unavailable', 'Проверка безопасности временно недоступна') from exc
    return response.rstrip(b'\0')


def signature_status(response, now=None):
    """clamd VERSION uses daemon local time; deployment fixes daemon TZ=UTC."""
    try:
        engine, version, built = response.decode('ascii').split('/')
        if not re.fullmatch(r'ClamAV [0-9][A-Za-z0-9.+~-]{0,63}', engine) or not version.isdecimal():
            raise ValueError('Malformed scanner version')
        updated = datetime.strptime(built, '%a %b %d %H:%M:%S %Y').replace(tzinfo=timezone.utc)
    except (UnicodeError, ValueError) as error:
        raise ApiError(503, 'scanner_version_invalid', 'Версия антивирусных баз недоступна') from error
    age = ((now or datetime.now(timezone.utc)) - updated).total_seconds()
    details = {'engine_version': engine.removeprefix('ClamAV '), 'signature_version': int(version),
               'signature_updated_at': updated.isoformat(), 'signature_age_seconds': int(age)}
    if age < -300:
        raise ApiError(503, 'scanner_clock_invalid', 'Время антивирусных баз некорректно', details)
    if age > get_settings().clamav_max_signature_age_hours * 3600:
        raise ApiError(503, 'scanner_signatures_stale', 'Антивирусные базы требуют обновления', details)
    return details


async def scanner_status():
    return signature_status(await scanner_command(b'VERSION'))


async def scan(data):
    await scanner_status()
    response = await scanner_command(b'INSTREAM', data)
    if response == b'stream: OK':
        return
    if response.endswith(b' FOUND'):
        raise ApiError(422, 'malware_detected', 'Файл отклонён проверкой безопасности')
    raise ApiError(503, 'scanner_unavailable', 'Проверка безопасности не завершена')


def parse_document(filename, data):
    """Runs only in a short-lived process; no file extraction or network access."""
    extension = PurePosixPath(filename).suffix.lower()
    quality = {'ocr_performed': False, 'page_numbers': extension == '.pdf',
               'images_not_extracted': False, 'text_verified': False}
    if extension == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted or len(reader.pages) > 500:
            raise ValueError('Encrypted PDF or more than 500 pages')
        pages = []
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ''
            if len(text) > 200_000:
                raise ValueError('PDF page text limit')
            pages.append((number, text))
            if not text.strip() or '/XObject' in page.get('/Resources', {}):
                quality['images_not_extracted'] = True
    elif extension == '.docx':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            texts = []
            for name in archive.namelist():
                if name == 'word/document.xml' or (name.startswith(('word/header', 'word/footer')) and name.endswith('.xml')):
                    xml = archive.read(name)
                    if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                        raise ValueError('XML entity forbidden')
                    root = ElementTree.fromstring(xml)
                    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                    texts.extend(''.join(n.text or '' for n in p.findall('.//w:t', ns)) for p in root.findall('.//w:p', ns))
            pages = [(None, '\n'.join(texts))]
            quality['images_not_extracted'] = any(n.startswith('word/media/') for n in archive.namelist())
    else:
        pages = [(None, data.decode('utf-8-sig'))]
    if sum(len(text) for _, text in pages) > MAX_TEXT:
        raise ValueError('Extracted text limit')
    chunks = []
    for page, content in pages:
        content = ' '.join(content.split())
        chunks.extend({'page': page, 'content': content[start:start+1200]} for start in range(0, len(content), 1200))
    if not chunks:
        raise ValueError('no_text_ocr_required')
    quality['incomplete_extraction'] = quality['images_not_extracted']
    return {'chunks': chunks, 'quality': quality}


async def parse_bounded(filename, data):
    if sys.platform == 'win32':
        raise ApiError(422, 'parser_platform_unsupported', 'Безопасный разбор документов требует Linux worker с ограничением памяти')
    try:
        # Only bounded pipe coordination occupies a thread; parsing and memory/CPU
        # limits belong to the short-lived Linux child process.
        process = await asyncio.to_thread(subprocess.run, [sys.executable, '-m', 'deanery_api.storage', filename],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=35, check=False)
        output = process.stdout
        if process.returncode or len(output) > MAX_TEXT * 8:
            raise ApiError(422, 'parse_failed', 'Не удалось извлечь текст; сканам требуется OCR')
        return json.loads(output)
    except subprocess.TimeoutExpired as exc:
        raise ApiError(422, 'parse_limit', 'Превышен предел обработки документа') from exc


async def health():
    settings = get_settings()
    async def check(name, configured, operation):
        if not configured:
            return name, {'status': 'unconfigured'}
        try:
            async with asyncio.timeout(2):
                details = await operation()
            return name, {'status': 'ready', **(details if name == 'scanner' else {})}
        except ApiError as error:
            return name, {'status': 'unavailable', **({'reason': error.code, **(error.details or {})} if name == 'scanner' else {})}
        except Exception:
            return name, {'status': 'unavailable'}
    async def bge():
        response = await _http.get(settings.bge_url.rstrip('/')+'/health')
        response.raise_for_status()
        if response.json().get('status') != 'ok':
            raise ValueError('BGE not ready')
    async def vectors():
        response = await _http.get(settings.qdrant_url.rstrip('/')+'/collections')
        response.raise_for_status()
        names = {c['name'] for c in response.json()['result']['collections']}
        if settings.qdrant_collection in names:
            params = (await qdrant('GET'))['result']['config']['params']
            if params['vectors']['dense']['size'] != 1024 or 'sparse' not in params.get('sparse_vectors', {}):
                raise ValueError('Vector config mismatch')
    return dict(await asyncio.gather(
        check('s3', _s3 is not None, lambda: client().head_bucket(Bucket=settings.s3_bucket)),
        check('scanner', bool(settings.clamav_host), scanner_status),
        check('qdrant', bool(settings.qdrant_url), vectors), check('bge', bool(settings.bge_url), bge)))


async def embed(texts):
    response = await _http.post(get_settings().bge_url.rstrip('/') + '/predict', json={
        'text': texts, 'return_dense': True, 'return_sparse': True, 'return_colbert': False})
    response.raise_for_status()
    data = response.json()
    dense_rows, sparse_rows = data.get('vector'), data.get('sparse')
    if not isinstance(dense_rows, list) or not isinstance(sparse_rows, list) or len(dense_rows) != len(texts) or len(sparse_rows) != len(texts):
        raise ValueError('Invalid BGE batch')
    result = []
    for dense, sparse in zip(dense_rows, sparse_rows):
        if not isinstance(dense, list) or len(dense) != 1024 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in dense):
            raise ValueError('Invalid BGE dense vector')
        if not isinstance(sparse, dict) or len(sparse) > 100000:
            raise ValueError('Invalid BGE sparse vector')
        pairs = sorted((int(i), float(v)) for i, v in sparse.items())
        if any(i < 0 or i > 2**32-1 or not math.isfinite(v) for i, v in pairs) or len({i for i, _ in pairs}) != len(pairs):
            raise ValueError('Invalid BGE sparse values')
        result.append({'dense': dense, 'sparse': {'indices': [i for i, _ in pairs], 'values': [v for _, v in pairs]}})
    return result


async def qdrant(method, suffix='', **kwargs):
    settings = get_settings()
    url = settings.qdrant_url.rstrip('/') + '/collections/' + quote(settings.qdrant_collection, safe='') + suffix
    response = await _http.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json()


async def ensure_collection():
    try:
        value = await qdrant('GET')
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        try:
            await qdrant('PUT', json={'vectors': {'dense': {'size': 1024, 'distance': 'Cosine'}}, 'sparse_vectors': {'sparse': {}}})
        except httpx.HTTPStatusError as conflict:
            if conflict.response.status_code != 409:
                raise
        value = await qdrant('GET')
    params = value['result']['config']['params']
    if params['vectors']['dense']['size'] != 1024 or 'sparse' not in params.get('sparse_vectors', {}):
        raise ValueError('Incompatible Qdrant collection')


async def delete_index(version_id):
    try:
        await qdrant('POST', '/points/delete?wait=true', json={'filter': {'must': [{'key': 'version_id', 'match': {'value': str(version_id)}}]}})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise


if __name__ == '__main__':
    if sys.platform != 'win32':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
        resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    raw = sys.stdin.buffer.read(10_000_001)
    if len(raw) > 10_000_000:
        raise ValueError('Input size limit')
    sys.stdout.write(json.dumps(parse_document(sys.argv[1], raw), ensure_ascii=True))
