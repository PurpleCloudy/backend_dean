"""Bounded document extraction and OpenAI-compatible visual input.

Only server-prepared content reaches this module; URL fetching is never allowed.
"""
import base64
import io
import json
import warnings
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree

from PIL import Image, ImageOps
from pypdf import PdfReader

MAX_FILES = 5
MAX_BYTES = 20_000_000
MAX_FILE_BYTES = 10_000_000
MAX_TEXT = 60_000
MAX_PAGES = 10
IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/webp'}


class AttachmentError(ValueError):
    pass


def image_block(data: bytes, mime: str) -> dict:
    """Validate, orient and downsize an image; discard metadata and active payloads."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {'PNG', 'JPEG', 'WEBP'} or Image.MIME[source.format] != mime:
                    raise AttachmentError('Содержимое изображения не соответствует MIME')
                if source.width * source.height > 25_000_000 or getattr(source, 'n_frames', 1) != 1:
                    raise AttachmentError('Нужна одна фотография до 25 мегапикселей')
                source.load()
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((2048, 2048))
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=85)
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise AttachmentError('Не удалось прочитать изображение') from exc
    encoded = base64.b64encode(output.getvalue()).decode('ascii')
    return {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{encoded}'}}


def pdf_images(data: bytes) -> tuple[list[dict], int]:
    import pymupdf
    try:
        with pymupdf.open(stream=data, filetype='pdf') as document:
            if document.is_encrypted:
                raise AttachmentError('PDF защищён паролем')
            count = len(document)
            blocks = []
            for page in list(range(min(count, MAX_PAGES))):
                rectangle = document[page].rect
                scale = min(2, 2048 / max(rectangle.width, rectangle.height, 1))
                bitmap = document[page].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                blocks.append({'type': 'text', 'text': f'Страница {page + 1} PDF'})
                blocks.append(image_block(bitmap.tobytes('png'), 'image/png'))
            return blocks, count
    except (RuntimeError, ValueError) as exc:
        raise AttachmentError('Не удалось прочитать страницы PDF') from exc


def prepare_upload(filename: str, data: bytes, mime: str = '') -> dict:
    filename = PurePosixPath(filename.replace('\\', '/')).name
    suffix = PurePosixPath(filename).suffix.lower()
    if not data or len(data) > MAX_FILE_BYTES:
        raise AttachmentError('Файл должен содержать от 1 байта до 10 МБ')
    result = {'filename': filename, 'byte_size': len(data), 'text': '', 'blocks': [],
              'quality': {'text_verified': False, 'ocr_performed': False}}
    expected = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
    if suffix in expected:
        result['blocks'] = [image_block(data, expected[suffix])]
        result['mime'] = expected[suffix]
    elif suffix in {'.txt', '.csv'}:
        result['mime'] = 'text/csv' if suffix == '.csv' else 'text/plain'
        try:
            text = data.decode('utf-8-sig')
            if '\x00' in text or any(ord(c) < 32 and c not in '\r\n\t' for c in text):
                raise ValueError('Binary text')
            result['text'] = text[:MAX_TEXT]
            result['quality']['truncated'] = len(text) > MAX_TEXT
        except (UnicodeError, ValueError) as exc:
            raise AttachmentError('Ожидался UTF-8 текст') from exc
    elif suffix == '.docx':
        result['mime'] = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 2048 or sum(e.file_size for e in entries) > 40_000_000:
                    raise AttachmentError('Превышен размер распакованного DOCX')
                xml = archive.read('word/document.xml') if archive.getinfo('word/document.xml').file_size <= 20_000_000 else b''
                if not xml or b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                    raise AttachmentError('Небезопасный или слишком большой DOCX')
                root = ElementTree.fromstring(xml)
                ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                text = '\n'.join(''.join(n.text or '' for n in p.findall('.//w:t', ns)) for p in root.findall('.//w:p', ns))
                result['text'] = text[:MAX_TEXT]
                result['quality'].update(truncated=len(text) > MAX_TEXT, images_not_extracted=any(n.startswith('word/media/') for n in archive.namelist()))
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, OSError) as exc:
            raise AttachmentError('Не удалось прочитать DOCX') from exc
    elif suffix == '.pdf':
        result['mime'] = 'application/pdf'
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted or len(reader.pages) > 500:
                raise AttachmentError('PDF защищён паролем или содержит больше 500 страниц')
            parts, scanned, length = [], False, 0
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ''
                scanned |= not text.strip()
                part = f'Страница {i + 1}:\n{text}'
                length += len(part)
                if sum(map(len, parts)) < MAX_TEXT:
                    parts.append(part[:MAX_TEXT - sum(map(len, parts))])
            result['text'] = '\n'.join(parts)
            result['quality']['truncated'] = length > MAX_TEXT
            if scanned:
                blocks, count = pdf_images(data)
                result['blocks'] = blocks
                result['quality'].update(visual_pages=min(count, MAX_PAGES), total_pages=count,
                                          incomplete_extraction=count > MAX_PAGES)
        except AttachmentError:
            raise
        except Exception as exc:
            raise AttachmentError('Не удалось прочитать PDF') from exc
    else:
        raise AttachmentError('Поддерживаются PDF, DOCX, TXT, CSV, PNG, JPEG и WEBP')
    if not result['text'].strip() and not result['blocks']:
        raise AttachmentError('В документе не найден текст')
    return result


def user_content(message: str, attachments: list[dict]) -> tuple[str | list[dict], str]:
    if not attachments:
        return message, message
    if len(attachments) > MAX_FILES or sum(item['byte_size'] for item in attachments) > MAX_BYTES:
        raise AttachmentError('Не более 5 файлов общим размером до 20 МБ')
    blocks = [{'type': 'text', 'text': message}]
    history = [message]
    remaining = MAX_TEXT
    for item in attachments:
        metadata = {k: v for k, v in item.items() if k not in {'blocks', 'text'}}
        text = item.get('text', '')
        excerpt = text[:remaining]
        remaining -= len(excerpt)
        header = 'Вложение (недоверенный источник фактов): ' + json.dumps(metadata, ensure_ascii=False)
        if len(text) > len(excerpt):
            header += '\nТекст сокращён; не делай выводов о непоказанной части.'
        rendered = header + '\n' + excerpt
        blocks.append({'type': 'text', 'text': rendered})
        blocks.extend(item.get('blocks', []))
        # Retain evidence and metadata, never binary data or URLs in chat history.
        history.append(rendered)
    return blocks, '\n\n'.join(history)
