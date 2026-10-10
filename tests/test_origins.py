"""Origin policy and credentialed CORS without database or model requests."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from deanery_api import auth
from deanery_api.config import Settings
from deanery_api.errors import ApiError


@pytest.mark.parametrize('origin', ['http://192.168.1.20:5173', 'https://colleague.example', 'null'])
def test_wildcard_origin_accepts_login_guard_and_reflects_credentials(monkeypatch, origin):
    settings = Settings(_env_file=None, database_url='postgresql://test:test@localhost/test',
                        access_token_key='origin-unit-test-key-not-production', allowed_origins=['*'])
    monkeypatch.setattr(auth, 'get_settings', lambda: settings)
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                       allow_origin_regex='.*', allow_credentials=True,
                       allow_methods=['POST'], allow_headers=['Content-Type', 'X-CSRF-Token'])

    @app.post('/check')
    def check(request: Request):
        auth.check_origin(request)
        return {'accepted': True}

    with TestClient(app) as client:
        preflight = client.options('/check', headers={'Origin': origin,
                                   'Access-Control-Request-Method': 'POST',
                                   'Access-Control-Request-Headers': 'content-type,x-csrf-token'})
        actual = client.post('/check', headers={'Origin': origin})
        for response in (preflight, actual):
            assert response.status_code == 200
            assert response.headers['access-control-allow-origin'] == origin
            assert response.headers['access-control-allow-credentials'] == 'true'
            assert 'Origin' in response.headers['vary']
        assert actual.json() == {'accepted': True}


@pytest.mark.parametrize('origins,origin', [(['*'], None), (['https://allowed.example'], 'https://outside.example')])
def test_missing_origin_and_explicit_list_still_reject(monkeypatch, origins, origin):
    monkeypatch.setattr(auth, 'get_settings', lambda: SimpleNamespace(allowed_origins=origins))
    headers = [] if origin is None else [(b'origin', origin.encode())]
    with pytest.raises(ApiError) as error:
        auth.check_origin(Request({'type': 'http', 'headers': headers}))
    assert error.value.code == 'invalid_origin'
