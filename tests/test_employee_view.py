"""One employee key per HTTP row, including several simultaneous unit roles."""
import httpx
import psycopg
from psycopg.rows import dict_row
import pytest
from uuid import uuid4

from test_integration_support import settings


@pytest.fixture(scope='module')
def employee_api():
    env = settings()
    if env.get('RUN_EMPLOYEE_VIEW_TESTS') != '1':
        pytest.skip('Explicit isolated employee-view PostgreSQL/API verification required')
    db = psycopg.connect(env['TEST_DATABASE_URL'], row_factory=dict_row, autocommit=True)
    assert db.execute('SELECT version_num FROM public.alembic_version').fetchone()['version_num'] == '0006'
    client = httpx.Client(base_url=env['BASE_URL'], trust_env=False, timeout=30)
    headers = {}
    for role in ('ADMIN', 'STUDENT'):
        response = client.post('/api/v1/auth/login', headers={'Origin': 'http://localhost:5173'},
            json={'login': env['TEST_'+role+'_LOGIN'], 'password': env['TEST_'+role+'_PASSWORD']})
        assert response.status_code == 200
        headers[role] = {'Authorization': 'Bearer '+response.json()['access_token']}
    yield db, client, headers
    client.close()
    db.close()


def test_one_employee_preserves_zero_one_many_and_mixed_unit_roles(employee_api):
    db, client, headers = employee_api
    suffix = uuid4().hex[:12]
    person = db.execute("INSERT INTO deanery.person(last_name,first_name,gender,birth_date) VALUES('Employee','View test','M','1990-01-01') RETURNING person_id").fetchone()
    employee = db.execute("""INSERT INTO deanery.employee(person_id,personnel_number,position_id,hire_date)
        VALUES(%s,%s,(SELECT position_id FROM deanery.position WHERE category='administration' ORDER BY position_id LIMIT 1),'2020-01-01')
        RETURNING employee_id""", (person['person_id'], 'EV'+suffix)).fetchone()['employee_id']

    def get(role='ADMIN'):
        response = client.get('/api/v1/resources/v_employees', params={'employee_id': employee}, headers=headers[role])
        assert response.status_code == 200, response.text
        items = response.json()['items']
        assert len(items) == 1 and items[0]['employee_id'] == employee
        assert 'phone' not in items[0] and 'email' not in items[0]
        return items[0]

    base = get()
    assert base['unit'] is None
    first_name, second_name, department_name = 'EV1'+suffix, 'EV2'+suffix, 'EVD'+suffix
    first = db.execute('INSERT INTO deanery.institute(name,short_name,director_id) VALUES(%s,%s,%s) RETURNING institute_id',
        ('Employee-view first '+suffix, first_name, employee)).fetchone()['institute_id']
    assert get()['unit'] == 'Дирекция '+first_name
    db.execute('INSERT INTO deanery.institute(name,short_name,director_id) VALUES(%s,%s,%s)',
        ('Employee-view second '+suffix, second_name, employee))
    assert get()['unit'] == 'Дирекция '+first_name+'; Дирекция '+second_name
    db.execute('INSERT INTO deanery.dean_office_staff(employee_id,institute_id) VALUES(%s,%s)', (employee, first))
    expected = 'Деканат '+first_name+'; Дирекция '+first_name+'; Дирекция '+second_name
    after = get()
    assert after['unit'] == expected and get('STUDENT')['unit'] == expected
    assert {k: v for k, v in after.items() if k != 'unit'} == {k: v for k, v in base.items() if k != 'unit'}

    # Teaching and administration positions are mutually exclusive in the domain.
    teacher_person = db.execute("INSERT INTO deanery.person(last_name,first_name,gender,birth_date) VALUES('Employee','Teacher view test','M','1990-01-01') RETURNING person_id").fetchone()['person_id']
    teacher = db.execute("""INSERT INTO deanery.employee(person_id,personnel_number,position_id,hire_date)
        VALUES(%s,%s,(SELECT position_id FROM deanery.position WHERE category='teaching' ORDER BY position_id LIMIT 1),'2020-01-01')
        RETURNING employee_id""", (teacher_person, 'ET'+suffix)).fetchone()['employee_id']
    department = db.execute('INSERT INTO deanery.department(institute_id,name,short_name) VALUES(%s,%s,%s) RETURNING department_id',
        (first, 'Employee-view department '+suffix, department_name)).fetchone()['department_id']
    db.execute('INSERT INTO deanery.teacher(employee_id,department_id) VALUES(%s,%s)', (teacher, department))
    response = client.get('/api/v1/resources/v_employees', params={'employee_id': teacher}, headers=headers['STUDENT'])
    assert response.status_code == 200
    assert len(response.json()['items']) == 1 and response.json()['items'][0]['unit'] == department_name
    counts = db.execute('SELECT (SELECT count(*) FROM deanery.employee) employees,count(*) rows,count(DISTINCT employee_id) keys FROM deanery.v_employees').fetchone()
    assert counts['employees'] == counts['rows'] == counts['keys']


def test_employee_paging_keeps_unique_keys_and_permissions(employee_api):
    db, client, headers = employee_api
    ids, offset = [], 0
    while True:
        response = client.get('/api/v1/resources/v_employees', params={'limit': 3, 'offset': offset}, headers=headers['ADMIN'])
        assert response.status_code == 200
        page = response.json()
        ids.extend(item['employee_id'] for item in page['items'])
        if not page['has_more']:
            break
        offset += 3
    expected = [r['employee_id'] for r in db.execute('SELECT employee_id FROM deanery.employee ORDER BY employee_id').fetchall()]
    assert ids == expected and len(set(ids)) == len(ids)
    assert client.get('/api/v1/resources/v_employees').status_code == 401
    options = db.execute("SELECT reloptions FROM pg_class WHERE oid='deanery.v_employees'::regclass").fetchone()['reloptions']
    assert 'security_invoker=true' in options
    columns = db.execute("SELECT attname,format_type(atttypid,atttypmod) kind FROM pg_attribute WHERE attrelid='deanery.v_employees'::regclass AND attnum>0 ORDER BY attnum").fetchall()
    assert [row['attname'] for row in columns] == ['employee_id', 'personnel_number', 'full_name', 'position', 'category', 'unit',
        'employment_rate', 'hire_date', 'dismissal_date', 'phone', 'email']
    assert next(row['kind'] for row in columns if row['attname'] == 'unit') == 'character varying'
