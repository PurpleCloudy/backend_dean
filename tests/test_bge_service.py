from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from integrations import bge_service as bge


@pytest.fixture
def client(monkeypatch):
    calls = []

    def encode(text, **kwargs):
        calls.append((text, kwargs))
        return {'dense_vecs': SimpleNamespace(tolist=lambda: [[0.0] * 1024 for _ in text]),
                'lexical_weights': [{42: 0.25} for _ in text]}

    model = SimpleNamespace(tokenizer=lambda text, **kw: {'input_ids': [[1, 2] for _ in text]}, encode=encode)
    monkeypatch.setattr(bge.app.state, 'model', model, raising=False)
    yield TestClient(bge.app), calls, model


def test_contract_and_limits(client):
    http, calls, model = client
    assert http.get('/health').json()['status'] == 'ok'
    result = http.post('/predict', json={'text': ['Русский текст', 'Second text']})
    assert result.status_code == 200
    assert len(result.json()['vector']) == 2
    assert len(result.json()['vector'][0]) == 1024
    assert result.json()['sparse'] == [{'42': 0.25}, {'42': 0.25}]
    assert calls[0][1]['return_colbert_vecs'] is False
    for body in ({'text': []}, {'text': [' ']}, {'text': [123]}, {'text': ['a'] * 9},
                 {'text': ['a' * 32769]}, {'text': ['a'], 'return_dense': False},
                 {'text': ['a'], 'return_colbert': True}, {'text': ['a'], 'unknown': 1}):
        assert http.post('/predict', json=body).status_code == 422
    assert len(calls) == 1
    model.tokenizer = lambda *a, **k: {'input_ids': [[1] * 8193]}
    assert http.post('/predict', json={'text': ['long']}).status_code == 422
    assert len(calls) == 1


def test_busy_and_failure_release(client):
    http, _, model = client
    with bge.inference_lock:
        response = http.post('/predict', json={'text': ['a']})
        assert response.status_code == 503
        assert response.headers['retry-after'] == '1'
        assert http.get('/health').status_code == 200

    def fail(*args, **kwargs):
        raise RuntimeError('Inference failed')

    model.encode = fail
    with pytest.raises(RuntimeError, match='Inference failed'):
        http.post('/predict', json={'text': ['a']})
    assert not bge.inference_lock.locked()
    del bge.app.state.model
    assert http.get('/health').status_code == 503
    assert http.post('/predict', json={'text': ['a']}).status_code == 503
    bge.app.state.model = model
