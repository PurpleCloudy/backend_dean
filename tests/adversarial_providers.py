"""Deterministic HTTP protocol fixtures; these are NOT live LLM/BGE evaluations.

Run: python tests/adversarial_providers.py --port 8231
Send VERIFY_TOOL:{"name":"query_deanery","arguments":{"sql":"..."}} in a
user message to make the actual original agent invoke one controlled tool.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

LOCK = threading.Lock()
STATE = {'bge_status': 200, 'bge_shape': 'valid', 'delay_seconds': 0}
TRACE = os.environ.get('PROVIDER_TRACE', '')


def embeddings(texts):
    vectors, sparse = [], []
    for text in texts:
        counts = {}
        for token in re.findall(r'\w+', text.casefold()):
            key = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], 'big') % 65536
            counts[str(key)] = counts.get(str(key), 0.0) + 1.0
        dense = [0.0] * 1024
        for key, value in counts.items():
            dense[int(key) % 1024] += value
        if not counts:
            dense[0] = 1.0
        norm = math.sqrt(sum(value * value for value in dense))
        vectors.append([value / norm for value in dense])
        sparse.append(counts)
    return {'vector': vectors, 'sparse': sparse}


def completion(body):
    messages = body.get('messages', [])
    last_user = max((i for i, m in enumerate(messages) if m.get('role') == 'user'), default=-1)
    user = str(messages[last_user].get('content', '')) if last_user >= 0 else ''
    results = [m for m in messages[last_user + 1:] if m.get('role') == 'tool']
    marker = 'VERIFY_TOOL:'
    if marker in user and not results:
        command, _ = json.JSONDecoder().raw_decode(user.split(marker, 1)[1].lstrip())
        name = command['name']
        if name not in {'query_deanery', 'propose_sql_change', 'search_regulations'}:
            raise ValueError('Fixture only supports original controlled tool names')
        message = {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'verify-' + uuid.uuid4().hex, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(command['arguments'])}}]}
        finish = 'tool_calls'
    else:
        message = {'role': 'assistant', 'content': json.dumps({
            'fixture': True, 'tool_results': [m.get('content') for m in results],
            'prior_user_messages': last_user and sum(m.get('role') == 'user' for m in messages[:last_user]) or 0})}
        finish = 'stop'
    return {'id': 'chatcmpl-' + uuid.uuid4().hex, 'object': 'chat.completion',
            'created': int(time.time()), 'model': 'verification-fixture',
            'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self.reply(200 if self.path == '/health' else 404,
                   {'status': 'ok', 'provider': 'deterministic-verification-fixture', 'live_provider': False})

    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2_000_000:
                return self.reply(413, {'error': 'body_limit'})
            body = json.loads(self.rfile.read(size))
            with LOCK:
                state = STATE.copy()
                if TRACE:
                    with Path(TRACE).open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps({'path': self.path, 'request': body, 'at': time.time()}, ensure_ascii=False) + '\n')
            if self.path == '/control':
                if any(k not in STATE for k in body) or not 0 <= float(body.get('delay_seconds', 0)) <= 180:
                    raise ValueError('Invalid fixture control')
                with LOCK:
                    STATE.update(body)
                return self.reply(200, {'fixture': True})
            time.sleep(state['delay_seconds'])
            if self.path == '/predict':
                if state['bge_status'] != 200:
                    return self.reply(state['bge_status'], {'error': 'injected_fixture_failure'})
                texts = body.get('text')
                if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
                    raise ValueError('text must be an array of strings')
                result = embeddings(texts)
                if state['bge_shape'] == 'wrong_dimension':
                    result['vector'] = [v[:10] for v in result['vector']]
                elif state['bge_shape'] == 'missing_sparse':
                    result.pop('sparse')
                return self.reply(200, result)
            if self.path == '/v1/chat/completions':
                return self.reply(200, completion(body))
            return self.reply(404, {'error': 'unknown_fixture_endpoint'})
        except (ValueError, KeyError, TypeError) as exc:
            self.reply(422, {'error': str(exc)})


def self_check():
    v = embeddings(['same tokens', 'same tokens', 'other'])
    assert len(v['vector'][0]) == 1024 and v['vector'][0] == v['vector'][1]
    assert v['vector'][0] != v['vector'][2]
    request = {'messages': [{'role': 'user', 'content': 'VERIFY_TOOL:{"name":"query_deanery","arguments":{"sql":"SELECT 1"}}'}]}
    first = completion(request)['choices'][0]
    assert first['finish_reason'] == 'tool_calls'
    request['messages'].append({'role': 'tool', 'content': '[]'})
    assert completion(request)['choices'][0]['finish_reason'] == 'stop'
    print('Deterministic provider self-check passed (not a live-provider test).')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8231)
    parser.add_argument('--self-check', action='store_true')
    args = parser.parse_args()
    if args.self_check:
        self_check()
    else:
        ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
