import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pglast import parse_sql
from pydantic import ValidationError

from deanery_api import files, jobs, olap
from deanery_api.auth import Principal
from deanery_api.errors import ApiError


@pytest.mark.parametrize('cube', list(olap.CUBES))
def test_all_cube_sql_and_drill_have_bounded_single_statement(cube):
    spec = olap.CUBES[cube]
    body = olap.CubeRequest(cube=cube, dimensions=list(spec['dimensions'])[:3], measures=list(spec['measures']))
    for drill in (False, True):
        query, parameters = olap.query_sql(body, drill)
        rendered = query.as_string()
        assert len(parse_sql(rendered.replace('%s', '1'))) == 1
        assert parameters == [50, 0] and 'LIMIT %s OFFSET %s' in rendered
        assert 'FROM filtered' in rendered and 'jsonb_agg' in rendered


@pytest.mark.parametrize('update', [
    {'dimensions': ['group_name; DROP TABLE student']}, {'dimensions': ['group_name', 'group_name']},
    {'measures': ['password_hash']}, {'measures': ['student_count', 'student_count']},
    {'filters': [{'field': 'full_name', 'op': 'eq', 'value': 'private dimension'}]},
    {'filters': [{'field': 'course', 'value': '1'}]},
    {'filters': [{'field': 'course', 'value': True}]},
    {'filters': [{'field': 'course', 'op': 'in', 'value': []}]},
    {'filters': [{'field': 'course', 'op': 'gte', 'value': None}]},
])
def test_invalid_olap_selection_and_filter_rejected(update):
    with pytest.raises((ApiError, ValidationError)):
        olap.query_sql(olap.CubeRequest.model_validate({'cube': 'students', 'measures': ['student_count'], **update}))


def test_filter_values_are_parameters_and_null_has_explicit_semantics():
    attack = "x'); DROP TABLE deanery.student; --"
    body = olap.CubeRequest(cube='students', measures=['student_count'], filters=[
        {'field': 'group_name', 'value': attack}, {'field': 'course', 'op': 'in', 'value': [None, 1, 2]},
        {'field': 'institute', 'op': 'ne', 'value': None}])
    query, parameters = olap.query_sql(body)
    assert attack not in query.as_string() and parameters == [attack, [1, 2], 50, 0]
    assert '"course" IS NULL' in query.as_string() and '"institute" IS NOT NULL' in query.as_string()


@pytest.mark.parametrize('module,endpoint,checker', [(files, files.list_files, 'get_file'), (jobs, jobs.list_jobs, 'owned_job')])
def test_list_rechecks_acl_and_continues_after_an_empty_page(monkeypatch, module, endpoint, checker):
    ids = [{'id': uuid4()} for _ in range(251)]
    cursor = SimpleNamespace(fetchall=AsyncMock(return_value=ids))
    conn = SimpleNamespace(execute=AsyncMock(return_value=cursor))
    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield conn
    monkeypatch.setattr(module, 'transaction', transaction)
    monkeypatch.setattr(module, checker, AsyncMock(side_effect=ApiError(404, 'not_found', 'Revoked')))
    principal = Principal(1, 'test', frozenset({'admin'}), None, frozenset(), None, None)
    kwargs = {'purpose': None, 'state': None, 'title': None} if module is files else {'kind': None, 'status': None}
    result = asyncio.run(endpoint(limit=10, offset=7, principal=principal, **kwargs))
    assert result == {'items': [], 'limit': 10, 'offset': 7, 'next_offset': 257, 'has_more': True}
    assert getattr(module, checker).await_count == 250


@pytest.mark.parametrize('module,endpoint,checker', [(files, files.list_files, 'get_file'), (jobs, jobs.list_jobs, 'owned_job')])
@pytest.mark.parametrize('error', [ApiError(503, 'unavailable', 'Failure'), TimeoutError()])
def test_list_failure_is_not_disguised_as_empty_results(monkeypatch, module, endpoint, checker, error):
    conn = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchall=AsyncMock(return_value=[{'id': uuid4()}]))))
    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield conn
    monkeypatch.setattr(module, 'transaction', transaction)
    monkeypatch.setattr(module, checker, AsyncMock(side_effect=error))
    principal = Principal(1, 'test', frozenset({'admin'}), None, frozenset(), None, None)
    kwargs = {'purpose': None, 'state': None, 'title': None} if module is files else {'kind': None, 'status': None}
    with pytest.raises(ApiError) as caught:
        asyncio.run(endpoint(limit=10, offset=0, principal=principal, **kwargs))
    assert caught.value.status == 503
