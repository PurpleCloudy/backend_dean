"""Opt-in HTTP verification of the actual React resource and form contracts."""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4
import httpx
import psycopg
from psycopg.rows import dict_row
import pytest
from test_integration_support import settings, output_directory

@pytest.fixture(scope='module')
def web_api():
    env=settings()
    if env.get('RUN_FRONTEND_INTEGRATION_TESTS')!='1':
        pytest.skip('Explicit isolated synthetic frontend/API verification required')
    assert ':55435/' in env['TEST_DATABASE_URL'], 'Dedicated frontend QA database required'
    db=psycopg.connect(env['TEST_DATABASE_URL'],autocommit=True,row_factory=dict_row)
    db.execute('SET search_path=deanery,backend,public')
    assert db.execute('SELECT version_num FROM alembic_version').fetchone()['version_num']=='0006'
    client=httpx.Client(base_url=env['BASE_URL'],headers={'Origin':'http://localhost:5173'},trust_env=False,timeout=40)
    login=client.post('/api/v1/auth/login',json={'login':env['TEST_ADMIN_LOGIN'],'password':env['TEST_ADMIN_PASSWORD']})
    assert login.status_code==200
    client.headers['Authorization']='Bearer '+login.json()['access_token']
    cases=[]
    path=output_directory(env)/'frontend-http.json'
    def request(case,method,resource,expected=200,**kwargs):
        response=client.request(method,'/api/v1/'+resource,**kwargs)
        body=response.json() if response.content else None
        cases.append({'id':case,'method':method,'path':'/api/v1/'+resource,'query':kwargs.get('params'),
                      'payload':kwargs.get('json'),'expected_status':expected,'actual_status':response.status_code,
                      'response':body,'status':'PASS' if response.status_code==expected else 'FAIL'})
        path.write_text(json.dumps({'provider':'No inference; database/resource HTTP checks','at':datetime.now(timezone.utc).isoformat(),'cases':cases},ensure_ascii=False,indent=2,default=str),encoding='utf8')
        assert response.status_code==expected,(case,response.status_code,response.text)
        return body
    yield client,db,request,env
    client.close();db.close()

@pytest.mark.parametrize('view',['v_student_summary','v_groups','v_schedule_calendar','v_academic_works','v_document_queue','v_practice','v_employees'])
def test_frontend_view_loading_pagination_filter(web_api,view):
    _,_,q,_=web_api
    first=q('T01-'+view,'GET','resources/'+view,params={'limit':2})
    assert len(first['items'])==2 and first['has_more']
    second=q('T05-'+view,'GET','resources/'+view,params={'limit':2,'offset':2})
    assert second['items'] and second['items']!=first['items']
    empty=q('T01-empty-'+view,'GET','resources/'+view,params={'limit':1,'offset':100000})
    assert empty['items']==[] and not empty['has_more']
    key=next(iter(first['items'][0]))
    value=first['items'][0][key]
    if value is not None:
        matched=q('T03-'+view,'GET','resources/'+view,params={key:value,'limit':3})
        assert matched['items'] and all(r[key]==value for r in matched['items'])


def test_frontend_lookup_and_request_boundaries(web_api):
    _,_,q,_=web_api
    names=['v_teachers','v_employees','v_students','person','dean_office_staff','v_schedule_hours','v_curriculum','curriculum','study_program','specialty','study_form','classroom','pair_time','academic_term','organization_contact','organization','academic_order','position','funding_type','leave_reason','request_status','document_type','holiday']
    for name in names:
        page=q('T07-'+name,'GET','resources/'+name,params={'limit':1})
        assert isinstance(page['items'],list)
    for params in ({'limit':501},{'offset':-1},{'sort':'password_hash'},{'student_id':'invalid'},{'unexpected':'filter'}):
        q('T08-invalid-filter','GET','resources/student',expected=422,params=params)
    q('S01-protected-group','PATCH','resources/student',expected=422,params={'student_id':1},json={'group_id':1})
    q('R03-no-delete-route','DELETE','resources/document_request',expected=405,params={'request_id':1})


def test_frontend_updates_and_rejected_domains(web_api):
    _,db,q,_=web_api
    checks=[('student','student_id','needs_dormitory',lambda x:not x),
            ('academic_work','work_id','topic',lambda x:'QA update '+uuid4().hex),
            ('practice_placement','placement_id','end_date',lambda x:(x+timedelta(days=1)).isoformat()),
            ('employee','employee_id','employment_rate',lambda x:'0.75' if str(x)!='0.75' else '1.00'),
            ('document_request','request_id','copies',lambda x:2 if x!=2 else 1)]
    for table,key,field,change in checks:
        before=db.execute(f'SELECT * FROM deanery.{table} ORDER BY {key} LIMIT 1').fetchone()
        value=change(before[field])
        q('T08-update-'+table,'PATCH','resources/'+table,params={key:before[key]},json={field:value})
        after=q('T06-readback-'+table,'GET','resources/'+table,params={key:before[key]})['items'][0]
        assert str(after[field]).lower()==str(value).lower()
        restore=before[field].isoformat() if hasattr(before[field],'isoformat') else str(before[field]) if table=='employee' else before[field]
        q('T08-restore-'+table,'PATCH','resources/'+table,params={key:before[key]},json={field:restore})
    work=db.execute('SELECT * FROM deanery.academic_work ORDER BY work_id LIMIT 1').fetchone()
    q('W02-reviewer-equals-supervisor','PATCH','resources/academic_work',expected=422,params={'work_id':work['work_id']},json={'reviewer_id':work['supervisor_id']})
    placement=db.execute('SELECT * FROM deanery.practice_placement ORDER BY placement_id LIMIT 1').fetchone()
    q('P01-equal-dates','PATCH','resources/practice_placement',expected=422,params={'placement_id':placement['placement_id']},json={'end_date':placement['start_date'].isoformat()})
    q('E01-rate-range','PATCH','resources/employee',expected=422,params={'employee_id':1},json={'employment_rate':'2.00'})
    q('R01-zero-copies','PATCH','resources/document_request',expected=422,params={'request_id':1},json={'copies':0})
    assert db.execute('SELECT reviewer_id FROM deanery.academic_work WHERE work_id=%s',(work['work_id'],)).fetchone()['reviewer_id']==work['reviewer_id']
    assert db.execute('SELECT end_date FROM deanery.practice_placement WHERE placement_id=%s',(placement['placement_id'],)).fetchone()['end_date']==placement['end_date']


def test_frontend_create_delete_group_employee_work_practice_schedule(web_api):
    _,db,q,_=web_api
    curriculum=db.execute('SELECT curriculum_id FROM deanery.curriculum ORDER BY curriculum_id LIMIT 1').fetchone()['curriculum_id']
    prefix=db.execute('SELECT deanery.fn_group_prefix(%s) p',(curriculum,)).fetchone()['p']
    name=db.execute("SELECT %s||lpad(n::text,2,'0') name FROM generate_series(1,99)n WHERE NOT EXISTS(SELECT FROM deanery.study_group WHERE name=%s||lpad(n::text,2,'0')) LIMIT 1",(prefix,prefix)).fetchone()['name']
    group=q('G02-create','POST','resources/study_group',json={'name':name,'curriculum_id':curriculum})
    q('G02-duplicate','POST','resources/study_group',expected=409,json={'name':name,'curriculum_id':curriculum})
    q('G02-update','PATCH','resources/study_group',params={'group_id':group['group_id']},json={'is_archived':True})
    q('G02-delete','DELETE','resources/study_group',params={'group_id':group['group_id']})
    q('G02-dependency-denial','DELETE','resources/study_group',expected=409,params={'group_id':1})
    person=q('E01-create-person','POST','resources/person',json={'last_name':'QA','first_name':'Employee','gender':'M','birth_date':'1990-01-01'})
    position=db.execute('SELECT position_id FROM deanery.position ORDER BY position_id LIMIT 1').fetchone()['position_id']
    emp=q('E01-create','POST','resources/employee',json={'person_id':person['person_id'],'personnel_number':'Q'+uuid4().hex[:12],'position_id':position,'employment_rate':'1.00','hire_date':'2025-01-01'})
    q('E02-linked-person','PATCH','resources/person',params={'person_id':person['person_id']},json={'first_name':'Updated'})
    q('E02-employee','PATCH','resources/employee',params={'employee_id':emp['employee_id']},json={'employment_rate':'0.50'})
    q('E03-delete','DELETE','resources/employee',params={'employee_id':emp['employee_id']})
    q('E03-person-cleanup','DELETE','resources/person',params={'person_id':person['person_id']})
    teacher=db.execute('SELECT t.employee_id FROM deanery.teacher t JOIN deanery.employee e USING(employee_id) WHERE e.dismissal_date IS NULL ORDER BY t.employee_id LIMIT 1').fetchone()['employee_id']
    for table,key,kind in [('academic_work','work_id','coursework'),('practice_placement','placement_id','practice')]:
        candidate=db.execute(f'''SELECT s.student_id,ci.item_id FROM deanery.student s JOIN deanery.study_group g USING(group_id)
        JOIN deanery.curriculum_item ci USING(curriculum_id) JOIN deanery.discipline d USING(discipline_id)
        WHERE d.kind=%s AND NOT EXISTS(SELECT FROM deanery.{table} x WHERE x.student_id=s.student_id AND x.item_id=ci.item_id)
        ORDER BY s.student_id,ci.item_id LIMIT 1''',(kind,)).fetchone()
        assert candidate,table
        body={**candidate,'supervisor_id':teacher}
        if table=='academic_work':body['topic']='QA created '+uuid4().hex
        else:
            contact=db.execute('SELECT org_contact_id FROM deanery.organization_contact ORDER BY org_contact_id LIMIT 1').fetchone()['org_contact_id']
            body.update(org_contact_id=contact,start_date='2026-10-12',end_date='2026-10-30')
        created=q(('W01' if table=='academic_work' else 'P01')+'-create','POST','resources/'+table,json=body)
        q('T08-duplicate-'+table,'POST','resources/'+table,expected=409,json=body)
        q('T09-delete-'+table,'DELETE','resources/'+table,params={key:created[key]})
    slot=db.execute('''SELECT a.assignment_id,cr.classroom_id,t.term_id,w.weekday,p.pair_number
    FROM deanery.teaching_assignment a CROSS JOIN deanery.academic_term t CROSS JOIN deanery.classroom cr
    CROSS JOIN generate_series(1,6) w(weekday) CROSS JOIN deanery.pair_time p
    WHERE cr.capacity>=60 AND NOT EXISTS(SELECT FROM deanery.schedule_slot s JOIN deanery.teaching_assignment a2 USING(assignment_id)
      WHERE s.term_id=t.term_id AND s.weekday=w.weekday AND s.pair_number=p.pair_number
        AND (a2.teacher_id=a.teacher_id OR a2.group_id=a.group_id OR s.classroom_id=cr.classroom_id))
    ORDER BY t.term_id,a.assignment_id,w.weekday DESC,p.pair_number DESC LIMIT 1''').fetchone()
    created=q('C03-create','POST','resources/schedule_slot',json=slot)
    q('C03-conflict','POST','resources/schedule_slot',expected=409,json=slot)
    q('C04-update','PATCH','resources/schedule_slot',params={'slot_id':created['slot_id']},json={'week_parity':'odd'})
    q('C04-delete','DELETE','resources/schedule_slot',params={'slot_id':created['slot_id']})


def test_student_document_request_exact_frontend_payload(web_api):
    _,db,q,env=web_api
    with httpx.Client(base_url=env['BASE_URL'],headers={'Origin':'http://localhost:5173'},trust_env=False,timeout=30)as student:
        login=student.post('/api/v1/auth/login',json={'login':env['TEST_STUDENT_LOGIN'],'password':env['TEST_STUDENT_PASSWORD']})
        assert login.status_code==200
        student.headers['Authorization']='Bearer '+login.json()['access_token']
        me=student.get('/api/v1/auth/me').json()
        status=db.execute('SELECT request_status_id FROM deanery.request_status WHERE NOT is_final ORDER BY request_status_id LIMIT 1').fetchone()['request_status_id']
        dtype=db.execute('SELECT document_type_id FROM deanery.document_type ORDER BY document_type_id LIMIT 1').fetchone()['document_type_id']
        body={'student_id':me['student_id'],'document_type_id':dtype,'request_status_id':status,'copies':1,'purpose':'QA student request'}
        response=student.post('/api/v1/resources/document_request',json=body)
        assert response.status_code==200,response.text
        assert response.json()['student_id']==me['student_id']
        foreign=db.execute('SELECT student_id FROM deanery.student WHERE student_id<>%s ORDER BY student_id LIMIT 1',(me['student_id'],)).fetchone()['student_id']
        assert student.post('/api/v1/resources/document_request',json={**body,'student_id':foreign}).status_code==403
        assert student.patch('/api/v1/resources/document_request',params={'request_id':response.json()['request_id']},json={'copies':2}).status_code==403
        assert student.post('/api/v1/resources/document_request',json={**body,'copies':0}).status_code==422



