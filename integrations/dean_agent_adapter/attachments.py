"""Consume immutable current-run versions through the existing signed backend API."""
import hashlib
import json
import subprocess
import sys
from uuid import UUID

from dean_agent.attachments import MAX_BYTES, MAX_FILE_BYTES, user_content
from .security import sign

INITIAL_CHUNKS = 1
TOOL_CHUNKS = 2
EXTRA_CHUNKS = 5
ATTACHED_MESSAGE_CHARS = 2000


class AttachmentContextError(ValueError):
    pass


def private_history_content(message, attachments):
    content, _ = user_content(message, attachments)
    # Native history must not grant future turns a second, unchecked file read.
    return content, message


def bounded_history(messages, budget=6000):
    """Keep newest complete old turns; current multimodal input is never sliced."""
    current, old = messages[-1], messages[:-1]
    pairs, position = [], 0
    while position + 1 < len(old):
        pair = old[position:position+2]
        if [item.get('role') for item in pair] == ['user', 'assistant']:
            pairs.append(pair)
            position += 2
        else:
            position += 1
    kept, used = [], 0
    for pair in reversed(pairs):
        size = sum(len(str(item.get('content', '')).encode('utf-8')) for item in pair)
        if used + size > budget:
            break
        kept[:0] = pair
        used += size
    return [*kept, current]


def render_visual(data, mime):
    if sys.platform != 'linux':
        raise AttachmentContextError('Visual rendering requires the bounded Linux deployment')
    try:
        result = subprocess.run([sys.executable, '-m', 'deanery_api.storage', '--visual', mime],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=35, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AttachmentContextError('Visual rendering time limit') from exc
    if result.returncode or len(result.stdout) > 12_000_000:
        raise AttachmentContextError('Visual rendering failed or exceeded its limit')
    return json.loads(result.stdout)


class AttachmentReader:
    def __init__(self, http, backend, secret, claims, vision=False):
        self.http, self.backend, self.secret, self.claims = http, backend, secret, claims
        self.vision, self.versions, self.remaining = vision, {}, EXTRA_CHUNKS

    def get(self, suffix='', *, params=None, binary=False):
        token = sign(self.secret, self.claims, 'deanery-attachments', 60)
        with self.http.stream('GET', self.backend + '/api/v1/internal/agent/attachments' + suffix,
                             headers={'X-Delegation': token}, params=params) as response:
            response.raise_for_status()
            limit, raw = (MAX_FILE_BYTES if binary else 1_000_000), bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > limit:
                    raise AttachmentContextError('Attachment response too large')
        return bytes(raw) if binary else json.loads(raw)

    def download(self, item):
        raw = self.get('/' + item['version_id'] + '/download', binary=True)
        if len(raw) != item['byte_size'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise AttachmentContextError('Original size or checksum mismatch')
        return raw

    def text(self, version_id, offset=0, limit=1, *, initial=False):
        from deanery_api.agent_gateway import AttachmentText
        version_id = str(UUID(version_id))
        if version_id not in self.versions or type(offset) is not int or not 0 <= offset <= 10000 or type(limit) is not int or not 1 <= limit <= TOOL_CHUNKS:
            raise AttachmentContextError('Attachment is not bound or pagination is invalid')
        if not initial and limit > self.remaining:
            return {'error': 'context_budget_exhausted', 'remaining_chunks': self.remaining,
                    'message': 'Лимит контекста: оставшаяся часть не прочитана. Задайте отдельный вопрос с этим вложением.'}
        value = AttachmentText.model_validate(self.get('/' + version_id + '/text', params={'offset': offset, 'limit': limit})).model_dump(mode='json')
        item = self.versions[version_id]
        if not value['text_available'] and item['mime'] == 'text/csv':
            # Exports have no indexed chunks; each read still rechecks ACL and hash.
            text = self.download(item).decode('utf-8-sig')
            value['chunks'] = [{'ordinal': n, 'page': None, 'text': text[n*1200:(n+1)*1200]}
                               for n in range(offset, offset+limit) if n*1200 < len(text)]
            value['next_offset'] = offset+limit if (offset+limit)*1200 < len(text) else None
            value['text_available'] = bool(text)
        chunks = value['chunks']
        if value['version_id'] != version_id or len(chunks) > limit or any(c['ordinal'] != offset+i or len(c['text']) > 1200 for i,c in enumerate(chunks)):
            raise AttachmentContextError('Invalid attachment text response')
        if value['next_offset'] is not None and value['next_offset'] != offset+len(chunks):
            raise AttachmentContextError('Invalid attachment continuation')
        if not initial:
            self.remaining -= len(chunks)
        return value

    def prepare(self, references):
        from deanery_api.agent_gateway import AttachmentContext
        context = AttachmentContext.model_validate(self.get()).model_dump(mode='json')
        if context['backend_run_id'] != self.claims['run'] or context['session_id'] != self.claims['session']:
            raise AttachmentContextError('Attachment run mismatch')
        items = context['attachments']
        expected = [(r['file_id'], r['version_id']) for r in references]
        if [(i['file_id'], i['version_id']) for i in items] != expected or not 1 <= len(items) <= 5 or sum(i['byte_size'] for i in items) > MAX_BYTES or any(not 0 < i['byte_size'] <= MAX_FILE_BYTES for i in items):
            raise AttachmentContextError('Attachment references or sizes mismatch')
        if [item['ordinal'] for item in items] != list(range(len(items))):
            raise AttachmentContextError('Attachment order mismatch')
        self.versions = {i['version_id']: i for i in items}
        prepared = []
        for item in items:
            entry = {**item, 'text': '', 'blocks': [], 'initial_excerpt_only': True}
            if item['text_available'] or item['mime'] == 'text/csv':
                value = self.text(item['version_id'], limit=INITIAL_CHUNKS, initial=True)
                entry['text'] = '\n\n'.join(f"Страница {c['page']}, фрагмент {c['ordinal']}:\n{c['text']}" for c in value['chunks'])
                entry['next_offset'] = value['next_offset']
            required = item['mime'].startswith('image/') or item['mime'] == 'application/pdf' and not item['text_available']
            if required and not self.vision:
                raise AttachmentContextError('agent_vision_unsupported')
            visual = required or (self.vision and item['mime'] == 'application/pdf' and (item['quality'] or {}).get('images_not_extracted'))
            if visual:
                result = render_visual(self.download(item), item['mime'])
                entry.update(blocks=result['blocks'], visual_pages=min(result['total_pages'], 10),
                             visual_truncated=result['total_pages'] > 10)
            prepared.append(entry)
        return prepared
