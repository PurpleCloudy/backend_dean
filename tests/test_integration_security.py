"""Small executable checks for boundaries independent of live service fixtures."""
import io
import os
from uuid import uuid4
import zipfile

import pytest

os.environ.setdefault('DATABASE_URL', 'postgresql://test:test@127.0.0.1/test')
os.environ.setdefault('ACCESS_TOKEN_KEY', 'integration-test-key-only-not-production')
os.environ.setdefault('DEVELOPMENT', 'true')

from deanery_api.agent_tools import validate_read_query, parse_change
from deanery_api.auth import Principal
from deanery_api.errors import ApiError
from deanery_api.storage import validate_document, parse_document
from integrations.dean_agent_adapter.security import sign, verify

PRINCIPAL = Principal(1, 'test', frozenset({'admin'}), None, frozenset(), None, None)


def test_query_projects_columns_and_nested_sources():
    query, limit, resources = validate_read_query(
        'SELECT s.student_id FROM deanery.student s JOIN (SELECT student_id FROM deanery.student) t ON s.student_id=t.student_id LIMIT 5', PRINCIPAL)
    assert limit == 5 and resources == {'student'}
    assert query.count('FROM deanery.student') == 2
    assert 'AS s' in query and 'record_book_number' in query
    metadata, _, _ = validate_read_query('SELECT table_name,column_name FROM information_schema.columns LIMIT 60', PRINCIPAL)
    assert 'information_schema.columns' not in metadata
    assert 'password_hash' not in metadata and 'birth_date' not in metadata


def test_agent_audit_projection_excludes_free_payloads():
    from deanery_api.agent_tools import sanitized_metadata
    query, _, resources = validate_read_query('SELECT audit_id,app_user_id,agent_request_id FROM deanery.v_audit LIMIT 5', PRINCIPAL)
    assert resources == {'v_audit'}
    assert all(field not in query for field in ('request_text','response_text','changed_by'))
    assert 'old_value' in query and 'new_value' in query  # SQL view itself sanitizes exact business pairs.
    metadata = sanitized_metadata(PRINCIPAL)
    assert all("'"+field+"'" not in metadata for field in ('request_text','response_text','changed_by'))
    assert "'v_audit','agent_request'" not in metadata
    for table, field in [('v_audit','agent_request'),
                         ('audit_log_detail','old_value'),('agent_request','request_text'),('agent_request','response_text')]:
        with pytest.raises(ApiError): validate_read_query(f'SELECT {field} FROM deanery.{table} LIMIT 5', PRINCIPAL)
    for table in ('audit_log','audit_log_detail'):
        with pytest.raises(ApiError): validate_read_query(f'SELECT 1 FROM deanery.{table} LIMIT 1', PRINCIPAL)
        with pytest.raises(ApiError): validate_read_query(f'SELECT count(1) FROM deanery.{table} LIMIT 1', PRINCIPAL)


@pytest.mark.parametrize('query', [
    'SELECT student_id FROM deanery.student',
    'SELECT * FROM deanery.student LIMIT 5',
    'SELECT password_hash FROM deanery.app_user LIMIT 5',
    'SELECT birth_date FROM deanery.person LIMIT 5',
    "SELECT set_config('app.user_id','1',true) LIMIT 5",
    "SELECT backend.set_actor(1,'x') LIMIT 5",
    'SELECT relname FROM pg_catalog.pg_class LIMIT 5',
    "SELECT 'deanery.student'::regclass LIMIT 5",
    'WITH pg_class AS (SELECT relname AS relname FROM pg_class) SELECT relname FROM pg_class LIMIT 5',
    "SELECT current_user LIMIT 5",
    'SELECT student_id FROM deanery.student FOR UPDATE LIMIT 5',
    'SELECT student_id FROM deanery.student; SELECT student_id FROM deanery.student LIMIT 5',
])
def test_query_rejects_bypasses(query):
    with pytest.raises(ApiError):
        validate_read_query(query, PRINCIPAL)


def test_proposals_literal_only_and_grade_sheet_protected():
    parsed = parse_change('UPDATE deanery.grade SET points = 4 WHERE grade_id = 5')
    assert parsed['table'] == 'grade' and parsed['key'] == 5 and parsed['values'] == {'points': 4}
    for query in ["UPDATE deanery.grade_sheet SET status='closed' WHERE sheet_id=1",
                  'UPDATE deanery.grade SET points=points+1 WHERE grade_id=5',
                  'UPDATE deanery.grade SET points=5 WHERE student_id=1',
                  'DELETE FROM deanery.grade WHERE grade_id=1']:
        with pytest.raises(ApiError):
            parse_change(query)


def test_grade_sheet_sql_maps_exact_null_and_date_semantics():
    from deanery_api.agent_tools import sheet_commands
    change=parse_change("UPDATE deanery.grade_sheet SET examiner_id=2,exam_date=NULL,status='closed',closed_date='2026-10-06' WHERE sheet_id=1")
    assert sheet_commands(change)==[('update_grade_sheet',{'sheet_id':1,'examiner_id':2,'exam_date':None}),
                                    ('close_grade_sheet',{'sheet_id':1,'closed_date':'2026-10-06'})]
    assert sheet_commands(parse_change("UPDATE deanery.grade_sheet SET examiner_id=2 WHERE sheet_id=1"))==[('update_grade_sheet',{'sheet_id':1,'examiner_id':2})]
    assert sheet_commands(parse_change("UPDATE deanery.grade_sheet SET status='cancelled',closed_date=NULL WHERE sheet_id=1"))==[('cancel_grade_sheet',{'sheet_id':1})]
    for query in ["UPDATE deanery.grade_sheet SET status='closed',closed_date=NULL WHERE sheet_id=1",
                  "UPDATE deanery.grade_sheet SET status='open',closed_date='2026-10-06' WHERE sheet_id=1",
                  "UPDATE deanery.grade_sheet SET examiner_id=NULL WHERE sheet_id=1",
                  "INSERT INTO deanery.grade_sheet(sheet_number,item_id,group_id,examiner_id) VALUES('S1',1,1,1)",
                  "INSERT INTO deanery.grade_sheet(sheet_number,issue_date,status) VALUES('S1','2026-10-06','closed')"]:
        with pytest.raises(ApiError):parse_change(query)


def test_delegation_forgery_expiry_audience_and_scope():
    secret = 'a-secret-only-for-this-test-123456789'
    claims = {'user': 1, 'session': str(uuid4()), 'run': str(uuid4())}
    token = sign(secret, claims, 'dean-agent')
    assert verify(secret, token, 'dean-agent')['user'] == 1
    for candidate, audience in [(token[:-3]+'abc', 'dean-agent'), (token, 'deanery-tools'),
                                (sign(secret, claims, 'dean-agent', -1), 'dean-agent')]:
        with pytest.raises(ValueError):
            verify(secret, candidate, audience)


def test_file_signatures_and_docx_limits():
    assert validate_document('file.txt', 'text/plain', 'текст'.encode()) == 'text/plain'
    for name, mime, raw in [('../x.txt', 'text/plain', b'a'), ('x.pdf', 'application/pdf', b'not pdf'),
                            ('x.txt', 'text/plain', b'\0binary'), ('x.docx', 'application/zip', b'PK')]:
        with pytest.raises(ApiError):
            validate_document(name, mime, raw)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', 'x' * 1_000_000)
    with pytest.raises(ApiError):
        validate_document('x.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', output.getvalue())
    assert parse_document('x.txt', b'One paragraph.')['chunks'] == [{'page': None, 'content': 'One paragraph.'}]


def test_csv_formula_and_exact_decimal():
    from decimal import Decimal
    from deanery_api.worker import render_csv
    csv = render_csv([{'formula': '=HYPERLINK("x")', 'number': Decimal('0.10000000000000001')}]).decode('utf-8-sig')
    assert "'=HYPERLINK" in csv and '0.10000000000000001' in csv


def test_native_parser_fails_closed_without_linux_memory_limits():
    import asyncio
    from unittest.mock import patch
    from deanery_api.storage import parse_bounded
    with patch('deanery_api.storage.sys.platform', 'win32'):
        with pytest.raises(ApiError) as error:
            asyncio.run(parse_bounded('untrusted.pdf', b'%PDF-1.4\n%%EOF'))
    assert error.value.code == 'parser_platform_unsupported'


@pytest.mark.parametrize('now,expected', [
    ('2026-10-06T07:00:00+00:00', None),
    ('2026-10-08T06:26:12+00:00', None),
    ('2026-10-08T06:26:13+00:00', 'scanner_signatures_stale'),
    ('2026-10-06T06:21:12+00:00', None),
    ('2026-10-06T06:21:11+00:00', 'scanner_clock_invalid'),
])
def test_scanner_signature_age(now, expected):
    from datetime import datetime
    from types import SimpleNamespace
    from unittest.mock import patch
    from deanery_api.storage import signature_status
    with patch('deanery_api.storage.get_settings', return_value=SimpleNamespace(clamav_max_signature_age_hours=48)):
        response = b'ClamAV 1.4.3/28145/Tue Oct  6 06:26:12 2026'
        if expected:
            with pytest.raises(ApiError) as error:
                signature_status(response, datetime.fromisoformat(now))
            assert error.value.code == expected
        else:
            result = signature_status(response, datetime.fromisoformat(now))
            assert result['signature_version'] == 28145 and result['engine_version'] == '1.4.3'


@pytest.mark.parametrize('response', [b'ClamAV 1.4.3', b'PONG', b'ClamAV 1.4.3/x/Tue Oct 6 06:26:12 2026',
                                      b'ClamAV 1.4.3/28145/not-a-date', b'\xff'])
def test_scanner_malformed_version_fails_closed(response):
    from deanery_api.storage import signature_status
    with pytest.raises(ApiError) as error:
        signature_status(response)
    assert error.value.code == 'scanner_version_invalid'


def test_scanner_unavailable_and_stale_block_before_scan():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch
    from deanery_api.storage import scan, scanner_status
    with patch('deanery_api.storage.get_settings', return_value=SimpleNamespace(clamav_host='127.0.0.1', clamav_port=1)), \
         patch('deanery_api.storage.asyncio.open_connection', new=AsyncMock(side_effect=ConnectionRefusedError)):
        with pytest.raises(ApiError) as error:
            asyncio.run(scanner_status())
        assert error.value.code == 'scanner_unavailable'
    command = AsyncMock(return_value=b'ClamAV 1.4.3/1/Mon Jan  1 00:00:00 2024')
    with patch('deanery_api.storage.scanner_command', new=command), \
         patch('deanery_api.storage.get_settings', return_value=SimpleNamespace(clamav_max_signature_age_hours=48)):
        with pytest.raises(ApiError) as error:
            asyncio.run(scan(b'clean document'))
        assert error.value.code == 'scanner_signatures_stale'
        assert command.await_args_list[0].args == (b'VERSION',) and command.await_count == 1
