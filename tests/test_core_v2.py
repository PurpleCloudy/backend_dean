"""Focused real HTTP/SQL checks against an explicitly supplied synthetic database.

Set BASE_URL, TEST_DATABASE_URL (owner), DATABASE_URL (nonowner runtime), and
TEST_{ADMIN,STAFF,DIRECTOR,TEACHER}_LOGIN/PASSWORD. No default credentials or DB.
"""
import concurrent.futures
import asyncio
import json
import os
import threading
import time
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row
import pytest


pytestmark=pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'),reason='Explicit synthetic HTTP database required')


def token():
    return 'T'+uuid.uuid4().hex[:17]


@pytest.fixture(scope='module')
def api():
    db=psycopg.connect(os.environ['TEST_DATABASE_URL'],row_factory=dict_row,autocommit=True)
    db.execute('SET search_path=deanery,backend,public')
    db.execute("SET TIME ZONE 'Europe/Moscow'")
    assert db.execute('SELECT version_num FROM alembic_version').fetchone()['version_num']=='0002'
    client=httpx.Client(base_url=os.environ['BASE_URL'],timeout=35,trust_env=False)
    headers={}
    for role in ('ADMIN','STAFF','DIRECTOR','TEACHER'):
        response=client.post('/api/v1/auth/login',headers={'Origin':'http://localhost:5173'},json={
            'login':os.environ['TEST_'+role+'_LOGIN'],'password':os.environ['TEST_'+role+'_PASSWORD']})
        assert response.status_code==200,response.text
        headers[role]={'Origin':'http://localhost:5173','Authorization':'Bearer '+response.json()['access_token']}
    out=Path(os.getenv('TEST_RESULTS_DIR','.local/test-results'));out.mkdir(parents=True,exist_ok=True)
    evidence=out/('core-v2-'+token()+'.jsonl')
    def request(name,body,role='ADMIN',expected=200,key=None,queue_delta_expected=None):
        jobs=db.execute('SELECT count(*) AS n FROM backend.jobs').fetchone()['n']
        response=client.post('/api/v1/workflows/'+name,json=body,headers={**headers[role],'Idempotency-Key':key or token()})
        result=response.json();rid=response.headers.get('X-Request-ID')
        audits=db.execute('SELECT audit_id,table_name,record_key,operation,app_user_id,request_id,agent_run_id,agent_request_id FROM audit_log WHERE request_id=%s ORDER BY audit_id',(rid,)).fetchall()
        queue_delta=db.execute('SELECT count(*) AS n FROM backend.jobs').fetchone()['n']-jobs
        record={'command':name,'body':body,'role':role,'expected_status':expected,'actual_status':response.status_code,'result':result,'audit':audits,'queue_delta':queue_delta}
        with evidence.open('a',encoding='utf-8')as f:f.write(json.dumps(record,ensure_ascii=False,default=str)+'\n')
        assert response.status_code==expected,record
        if expected>=400:assert not audits and queue_delta==0,record
        else:
            assert queue_delta==(queue_delta_expected if queue_delta_expected is not None else 1 if 'event_id'in result and result.get('status')=='pending'else 0),record
            assert all(a['request_id']==rid for a in audits)
        return result,audits
    yield db,client,headers,request,evidence
    client.close();db.close()


def new_group(db):
    source=db.execute('SELECT curriculum_id FROM study_group ORDER BY group_id LIMIT 1').fetchone()['curriculum_id']
    prefix=db.execute('SELECT fn_group_prefix(%s) AS p',(source,)).fetchone()['p']
    name=db.execute("SELECT %s||lpad(n::text,2,'0') name FROM generate_series(1,99)n WHERE NOT EXISTS(SELECT FROM study_group WHERE name=%s||lpad(n::text,2,'0')) LIMIT 1",(prefix,prefix)).fetchone()['name']
    return db.execute('INSERT INTO study_group(name,curriculum_id) VALUES(%s,%s) RETURNING group_id,name',(name,source)).fetchone()


def enrollment(group,today):
    return {'last_name':'Проверка','first_name':'Схемы','gender':'M','birth_date':'2000-01-01','group_name':group['name'],
        'funding':'Бюджет','record_book':token(),'order_number':token(),'signed_by':1,'effective_date':str(today)}


def test_future_orders_and_native_transitions(api):
    db,client,headers,request,_=api
    today=db.execute('SELECT current_date d').fetchone()['d'];group=new_group(db);other=new_group(db)
    payload=enrollment(group,today+timedelta(days=1));key=token()
    pending,audits=request('enroll_student',payload,'STAFF',key=key)
    sid=pending['student_id']
    assert pending['status']=='pending'
    assert client.get('/api/v1/workflows/scheduled/'+pending['event_id'],headers=headers['STAFF']).json()['job_id']==pending['job_id']
    db.execute("UPDATE backend.jobs SET status='failed',error_code='test_not_due' WHERE id=%s",(pending['job_id'],))
    failed=client.get('/api/v1/jobs/'+pending['job_id'],headers=headers['STAFF'])
    assert failed.status_code==200 and failed.json()['error_code']=='test_not_due'
    db.execute("UPDATE backend.jobs SET status='queued',error_code=NULL WHERE id=%s",(pending['job_id'],))
    assert db.execute('SELECT st.name,os.applied_at FROM student s JOIN student_status st USING(status_id) JOIN order_student os USING(student_id) WHERE s.student_id=%s',(sid,)).fetchone()=={'name':'Зачислен','applied_at':None}
    duplicate,duplicate_audits=request('enroll_student',payload,'STAFF',key=key,queue_delta_expected=0)
    assert duplicate==pending and duplicate_audits==[]
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row)as c:
        uid=db.execute('SELECT user_id FROM app_user WHERE login=%s',(os.environ['TEST_STAFF_LOGIN'],)).fetchone()['user_id']
        c.execute('SELECT backend.set_actor(%s,%s)',(uid,token()));c.commit()
        with pytest.raises(psycopg.errors.RaiseException):c.execute('SELECT backend.apply_scheduled_order(%s)',(pending['event_id'],))
    active,_=request('enroll_student',enrollment(group,today),'STAFF');sid=active['student_id']
    assert db.execute('SELECT applied_at FROM order_student WHERE order_id=%s',(active['order_id'],)).fetchone()['applied_at'] is not None
    body={'student_id':sid,'group_name':other['name'],'order_number':token(),'signed_by':1,'effective_date':str(today+timedelta(days=1))}
    transfer,_=request('transfer_student',body,'STAFF')
    assert db.execute('SELECT group_id FROM student WHERE student_id=%s',(sid,)).fetchone()['group_id']==group['group_id']
    request('cancel_scheduled',{'event_id':transfer['event_id']},'STAFF')
    assert not db.execute('SELECT 1 FROM order_student WHERE order_id=%s',(transfer['order_id'],)).fetchall()
    assert client.get('/api/v1/workflows/scheduled/'+transfer['event_id'],headers=headers['STAFF']).json()['status']=='cancelled'
    body['effective_date']=str(today);body['order_number']=token();request('transfer_student',body,'STAFF')
    assert db.execute('SELECT group_id FROM student WHERE student_id=%s',(sid,)).fetchone()['group_id']==other['group_id']
    leave,_=request('grant_academic_leave',{'student_id':sid,'reason':'Семейные обстоятельства','start':str(today-timedelta(days=1)),'end':str(today+timedelta(days=30)),'order_number':token(),'signed_by':1},'STAFF')
    assert db.execute('SELECT st.name FROM student s JOIN student_status st USING(status_id) WHERE student_id=%s',(sid,)).fetchone()['name']=='Академический отпуск'
    request('return_from_leave',{'student_id':sid,'order_number':token(),'signed_by':1,'effective_date':str(today)},'STAFF')
    saved=db.execute('SELECT end_date,fn_leave_actual_end(al) actual FROM academic_leave al WHERE student_id=%s',(sid,)).fetchone()
    assert saved=={'end_date':today+timedelta(days=30),'actual':today-timedelta(days=1)}
    request('expel_student',{'student_id':sid,'order_number':token(),'signed_by':1,'reason':'Заявление студента'},'STAFF')
    request('reinstate_student',{'student_id':sid,'group_name':group['name'],'order_number':token(),'signed_by':1},'STAFF')
    assert db.execute('SELECT st.name,s.group_id FROM student s JOIN student_status st USING(status_id) WHERE student_id=%s',(sid,)).fetchone()=={'name':'Обучается','group_id':group['group_id']}


def test_closed_grade_two_person_lifecycle(api):
    db,_,_,request,_=api
    grade=db.execute("""SELECT g.*,gs.sheet_number,s.record_book_number FROM grade g JOIN grade_sheet gs USING(sheet_id) JOIN student s USING(student_id)
      WHERE gs.examiner_id=6 AND gs.status='closed' AND NOT EXISTS(SELECT FROM grade_correction c WHERE c.grade_id=g.grade_id AND c.status IN('pending','approved'))
      AND NOT EXISTS(SELECT FROM grade later JOIN grade_sheet ls ON ls.sheet_id=later.sheet_id WHERE later.student_id=g.student_id AND ls.item_id=gs.item_id AND ls.stage=gs.stage AND ls.status<>'cancelled' AND (coalesce(ls.exam_date,'infinity'::date),ls.sheet_id)>(gs.exam_date,gs.sheet_id))
      ORDER BY gs.exam_date DESC,g.grade_id DESC LIMIT 1""").fetchone()
    value=grade['points']+1 if grade['points'] and grade['points']<50 else 42
    body={'sheet_number':grade['sheet_number'],'record_book':grade['record_book_number'],'new_points':value,'new_is_absent':False,'reason':'Ошибка переноса подтверждена документом'}
    request('request_grade_correction',{**body,'reason':'     '},'TEACHER',422)
    request('request_grade_correction',{**body,'new_points':999},'TEACHER',409)
    request('request_grade_correction',{**body,'new_points':grade['points'],'new_is_absent':grade['is_absent']},'TEACHER',422)
    result,audits=request('request_grade_correction',body,'TEACHER')
    cid=result['correction_id'];assert result['status']=='pending'
    assert db.execute('SELECT points,is_absent FROM grade WHERE grade_id=%s',(grade['grade_id'],)).fetchone()=={'points':grade['points'],'is_absent':grade['is_absent']}
    request('request_grade_correction',body,'TEACHER',409)
    request('decide_grade_correction',{'correction_id':cid,'approve':True},'TEACHER',403)
    request('decide_grade_correction',{'correction_id':cid,'approve':False},'DIRECTOR',422)
    applied,audits=request('decide_grade_correction',{'correction_id':cid,'approve':True,'comment':'Проверено по первичному документу'},'DIRECTOR')
    assert applied['status']=='applied'
    assert db.execute('SELECT points,is_absent FROM grade WHERE grade_id=%s',(grade['grade_id'],)).fetchone()=={'points':value,'is_absent':False}
    assert {a['table_name']for a in audits}=={'grade','grade_correction'}
    assert all(a['app_user_id']==1 for a in audits)
    assert db.execute('SELECT requested_by_id,decided_by_id FROM grade_correction WHERE correction_id=%s',(cid,)).fetchone()=={'requested_by_id':5,'decided_by_id':1}
    # A director can request; a different actual deputy must decide.
    result,_=request('request_grade_correction',{**body,'new_points':value+1},'DIRECTOR')
    request('decide_grade_correction',{'correction_id':result['correction_id'],'approve':True},'DIRECTOR',403)
    rejected,_=request('decide_grade_correction',{'correction_id':result['correction_id'],'approve':False,'comment':'Не подтверждено документами'},'STAFF')
    assert rejected['status']=='rejected'
    assert db.execute('SELECT points FROM grade WHERE grade_id=%s',(grade['grade_id'],)).fetchone()['points']==value


def test_contract_and_private_sql(api):
    db,client,headers,_,_=api
    doc=client.get('/openapi.json').json()
    def visit(value):
        if isinstance(value,dict):
            if '$ref'in value:
                target=doc
                for part in value['$ref'][2:].split('/'):target=target[part.replace('~1','/').replace('~0','~')]
            for v in value.values():visit(v)
        elif isinstance(value,list):
            for v in value:visit(v)
    visit(doc)
    for name in ('v_students','v_grade_corrections','v_pending_orders','v_active_scholarships','v_audit'):
        response=client.get('/api/v1/resources/'+name,params={'limit':1},headers=headers['ADMIN']);assert response.status_code==200,response.text
    uid=db.execute('SELECT user_id FROM app_user WHERE login=%s',(os.environ['TEST_ADMIN_LOGIN'],)).fetchone()['user_id']
    with psycopg.connect(os.environ['DATABASE_URL'])as c:
        c.execute('SELECT backend.set_actor(%s,%s)',(uid,token()));c.commit()
        for query in ('SELECT birth_date FROM deanery.person','SELECT request_text FROM deanery.agent_request','SELECT * FROM deanery.audit_log_detail'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with c.transaction():c.execute('SET LOCAL ROLE deanery_reader');c.execute(query)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():c.execute("INSERT INTO backend.events(action,resource,record_key) VALUES('grade_correction_requested','grade_correction','{}')")


def test_foreign_reinstatement_denied_before_any_effect(api):
    db,_,_,request,_=api
    today=db.execute('SELECT current_date d').fetchone()['d'];own=new_group(db)
    # Synthetic counterpart changes only the curriculum's public department link;
    # source guards remain enabled. It is genuinely another institute.
    foreign=db.execute('SELECT g.group_id,g.name,i.director_id FROM study_group g JOIN institute i ON i.institute_id=fn_group_institute(g.group_id) WHERE i.institute_id<>1 AND i.director_id IS NOT NULL AND NOT g.is_archived LIMIT 1').fetchone()
    if foreign is None:
        existing=db.execute("SELECT institute_id FROM institute WHERE group_letter='Я'").fetchone()
        institute=existing['institute_id'] if existing else db.execute("INSERT INTO institute(name,short_name,group_letter,director_id) VALUES(%s,%s,'Я',1) RETURNING institute_id",(token(),token())).fetchone()['institute_id']
        department=db.execute('INSERT INTO department(institute_id,name,short_name) VALUES(%s,%s,%s) RETURNING department_id',(institute,token(),token())).fetchone()['department_id']
        program=db.execute('INSERT INTO study_program(specialty_id,department_id,profile) SELECT specialty_id,%s,%s FROM study_program LIMIT 1 RETURNING program_id',(department,token())).fetchone()['program_id']
        curriculum=db.execute('INSERT INTO curriculum(program_id,study_form_id,start_year,duration_years) SELECT %s,study_form_id,start_year,duration_years FROM curriculum LIMIT 1 RETURNING curriculum_id',(program,)).fetchone()['curriculum_id']
        prefix=db.execute('SELECT fn_group_prefix(%s) p',(curriculum,)).fetchone()['p']
        foreign=db.execute('INSERT INTO study_group(name,curriculum_id) VALUES(%s,%s) RETURNING group_id,name',(prefix+'01',curriculum)).fetchone();foreign['director_id']=1
    body=enrollment(foreign,today);body['signed_by']=foreign['director_id']
    created,_=request('enroll_student',body)
    request('expel_student',{'student_id':created['student_id'],'order_number':token(),'signed_by':foreign['director_id'],'reason':'Тест разграничения доступа'})
    before=db.execute('SELECT to_jsonb(s) value FROM student s WHERE student_id=%s',(created['student_id'],)).fetchone()['value']
    request('reinstate_student',{'student_id':created['student_id'],'group_name':own['name'],'order_number':token(),'signed_by':1},'STAFF',403)
    assert db.execute('SELECT to_jsonb(s) value FROM student s WHERE student_id=%s',(created['student_id'],)).fetchone()['value']==before


def test_capacity_last_seat_native_race(api):
    db,_,_,_,evidence=api
    group=new_group(db);gid=group['group_id']
    room=db.execute("INSERT INTO classroom(building,room_number,capacity,room_kind) VALUES(%s,'1',1,'lecture') RETURNING classroom_id",(token(),)).fetchone()['classroom_id']
    start=db.execute('SELECT max(session_end)+10 d FROM academic_term').fetchone()['d']
    term=db.execute('INSERT INTO academic_term(name,start_date,end_date,session_start,session_end) VALUES(%s,%s,%s,%s,%s) RETURNING term_id',(token(),start,start+timedelta(days=30),start+timedelta(days=31),start+timedelta(days=40))).fetchone()['term_id']
    assignment=db.execute('INSERT INTO teaching_assignment(item_id,group_id,teacher_id,lesson_type_id) SELECT min(item_id),%s,6,(SELECT min(lesson_type_id) FROM lesson_type) FROM curriculum_item WHERE curriculum_id=(SELECT curriculum_id FROM study_group WHERE group_id=%s) RETURNING assignment_id',(gid,gid)).fetchone()['assignment_id']
    db.execute('INSERT INTO schedule_slot(assignment_id,classroom_id,weekday,pair_number,term_id) SELECT %s,%s,1,min(pair_number),%s FROM pair_time',(assignment,room,term))
    actor=db.execute('SELECT user_id FROM app_user WHERE login=%s',(os.environ['TEST_ADMIN_LOGIN'],)).fetchone()['user_id']
    ready=threading.Event();second_pid=[]
    def connect():
        c=psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row)
        c.execute('SET search_path=deanery,backend,public');c.execute("SET statement_timeout='8000ms'")
        c.execute('SELECT backend.set_actor(%s,%s)',(actor,token()));c.commit();return c
    def add(c):
        c.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        c.execute("SELECT backend.command('enroll_student')")
        person=c.execute("INSERT INTO person(last_name,first_name,gender,birth_date) VALUES('Capacity','Fixture','M','2000-01-01') RETURNING person_id").fetchone()['person_id']
        return c.execute("INSERT INTO student(person_id,record_book_number,group_id,funding_type_id,status_id,enrollment_date) SELECT %s,%s,%s,1,status_id,current_date FROM student_status WHERE name='Обучается' RETURNING student_id",(person,token(),gid)).fetchone()['student_id']
    def competitor():
        with connect()as c:
            second_pid.append(c.info.backend_pid);ready.set()
            try:add(c);c.commit();return 'committed'
            except psycopg.Error as exc:c.rollback();return exc.sqlstate
    with connect()as first,concurrent.futures.ThreadPoolExecutor(max_workers=1)as executor:
        inserted=add(first);pending=executor.submit(competitor);assert ready.wait(3)
        deadline=time.monotonic()+3;wait=None
        while time.monotonic()<deadline:
            wait=db.execute('SELECT wait_event FROM pg_stat_activity WHERE pid=%s',(second_pid[0],)).fetchone()
            if wait and wait['wait_event']=='advisory':break
            time.sleep(.03)
        assert wait and wait['wait_event']=='advisory',wait
        first.commit();assert pending.result(timeout=10)=='P0001'
    assert db.execute('SELECT student_id FROM student WHERE group_id=%s',(gid,)).fetchall()==[{'student_id':inserted}]
    with evidence.open('a',encoding='utf-8')as f:f.write(json.dumps({'case':'capacity-last-seat','isolation':'read committed','wait':wait,'second_sqlstate':'P0001','committed_student_id':inserted,'group_id':gid})+'\n')


def test_due_callback_is_exact_idempotent_and_rechecks_actor(api):
    db,_,_,request,evidence=api
    today=db.execute('SELECT current_date d').fetchone()['d'];group=new_group(db)
    students=[request('enroll_student',enrollment(group,today),'STAFF')[0]['student_id'] for _ in range(2)]
    actor=db.execute('SELECT user_id FROM app_user WHERE login=%s',(os.environ['TEST_STAFF_LOGIN'],)).fetchone()['user_id']
    events=[];orders=[]
    # Native fixture creates due pending orders without applying them, as a
    # worker would find after interruption. No trigger/constraint is disabled.
    for sid in students:
        with db.transaction():
            db.execute('SELECT backend.set_actor(%s,%s)',(actor,token()))
            db.execute("SELECT backend.command('expel_student')")
            oid=db.execute("SELECT fn_student_order(%s,'expel',%s,'Проверка очереди',1,'Заявление',p_effective=>current_date) id",(sid,token())).fetchone()['id']
        db.execute("SELECT backend.set_actor(NULL,'')")
        event_id=uuid.uuid4();db.execute("INSERT INTO backend.domain_events(id,user_id,name,order_id,student_id) VALUES(%s,%s,'expel_student',%s,%s)",(event_id,actor,oid,sid));events.append(event_id);orders.append(oid)
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row)as c:
        c.execute('SELECT backend.set_actor(%s,%s)',(actor,token()));c.commit()
        result=c.execute('SELECT backend.apply_scheduled_order(%s) result',(events[0],)).fetchone()['result'];c.commit()
        assert result['order_id']==orders[0]
        first_audit=db.execute("SELECT count(*) n FROM audit_log WHERE table_name='student' AND record_key=%s",('student_id='+str(students[0]),)).fetchone()['n']
        repeated=c.execute('SELECT backend.apply_scheduled_order(%s) result',(events[0],)).fetchone()['result'];c.commit();assert repeated==result
        assert db.execute("SELECT count(*) n FROM audit_log WHERE table_name='student' AND record_key=%s",('student_id='+str(students[0]),)).fetchone()['n']==first_audit
        assert db.execute('SELECT applied_at FROM order_student WHERE order_id=%s',(orders[1],)).fetchone()['applied_at'] is None
        db.execute('UPDATE app_user SET is_active=false WHERE user_id=%s',(actor,))
        try:
            with pytest.raises(psycopg.Error)as failure:c.execute('SELECT backend.apply_scheduled_order(%s)',(events[1],))
            assert failure.value.sqlstate in ('42501','P0002');c.rollback()
        finally:db.execute('UPDATE app_user SET is_active=true WHERE user_id=%s',(actor,))
    assert db.execute('SELECT applied_at FROM order_student WHERE order_id=%s',(orders[1],)).fetchone()['applied_at'] is None
    with evidence.open('a',encoding='utf-8')as f:f.write(json.dumps({'case':'due-exact-idempotent-current-actor','order_ids':orders,'student_ids':students,'applied_result':result,'unrelated_applied_at':None,'inactive_sqlstate':failure.value.sqlstate},default=str)+'\n')


def test_later_attempt_blocks_pending_correction_atomically(api):
    db,_,_,request,_=api
    grade=db.execute("""SELECT g.*,gs.sheet_number,gs.item_id,gs.group_id,gs.stage,gs.examiner_id,s.record_book_number
      FROM grade g JOIN grade_sheet gs USING(sheet_id) JOIN student s USING(student_id) JOIN student_status st USING(status_id)
      WHERE gs.status='closed' AND gs.stage='final' AND g.points IS NULL AND st.is_active AND s.group_id=gs.group_id
      AND NOT EXISTS(SELECT FROM grade_correction c WHERE c.grade_id=g.grade_id AND c.status IN('pending','approved'))
      AND NOT EXISTS(SELECT FROM grade later JOIN grade_sheet ls ON ls.sheet_id=later.sheet_id WHERE later.student_id=g.student_id AND ls.item_id=gs.item_id AND ls.stage=gs.stage AND ls.status<>'cancelled' AND (coalesce(ls.exam_date,'infinity'::date),ls.sheet_id)>(gs.exam_date,gs.sheet_id))
      ORDER BY g.grade_id DESC LIMIT 1""").fetchone();assert grade
    correction,_=request('request_grade_correction',{'sheet_number':grade['sheet_number'],'record_book':grade['record_book_number'],'new_points':30,'reason':'Проверка очередности попыток'},'STAFF')
    sheet,_=request('create_grade_sheet',{'sheet_number':token(),'item_id':grade['item_id'],'group_id':grade['group_id'],'examiner_id':grade['examiner_id'],'stage':'final','sheet_kind':'retake','exam_date':str(db.execute('SELECT current_date d').fetchone()['d'])})
    request('record_grade',{'sheet_id':sheet['sheet_id'],'student_id':grade['student_id'],'points':50})
    request('decide_grade_correction',{'correction_id':correction['correction_id'],'approve':True},'DIRECTOR',409)
    assert db.execute('SELECT points FROM grade WHERE grade_id=%s',(grade['grade_id'],)).fetchone()['points']==grade['points']
    assert db.execute('SELECT status FROM grade_correction WHERE correction_id=%s',(correction['correction_id'],)).fetchone()['status']=='pending'
    request('decide_grade_correction',{'correction_id':correction['correction_id'],'approve':False,'comment':'Появилась более поздняя попытка'},'DIRECTOR')


def test_shared_domain_transaction_uses_moscow_date(api):
    db,_,_,_,_=api
    actor=db.execute('SELECT user_id FROM app_user WHERE login=%s',(os.environ['TEST_ADMIN_LOGIN'],)).fetchone()['user_id']
    async def check():
        from deanery_api import db as runtime
        from deanery_api.auth import load_principal
        await runtime.open_pool()
        try:
            async with runtime.get_pool().connection()as conn:principal=await load_principal(conn,actor)
            async with runtime.transaction(principal,'midnight-boundary')as conn:
                row=await(await conn.execute("SELECT current_setting('TimeZone') zone,('2026-10-06 21:00:00+00'::timestamptz)::date d,current_setting('transaction_isolation') isolation")).fetchone()
                assert row['zone']=='Europe/Moscow' and str(row['d'])=='2026-10-07' and row['isolation']=='read committed'
        finally:await runtime.close_pool()
    asyncio.run(check(),**({'loop_factory':asyncio.SelectorEventLoop}if sys.platform=='win32'else {}))


def test_correction_student_scope_is_own_record(api):
    db,_,_,request,_=api
    grade=db.execute("""SELECT g.grade_id,g.points,gs.sheet_number,s.record_book_number FROM grade g JOIN grade_sheet gs USING(sheet_id) JOIN student s USING(student_id)
      WHERE s.student_id=1 AND gs.status='closed' AND NOT EXISTS(SELECT FROM grade_correction c WHERE c.grade_id=g.grade_id AND c.status IN('pending','approved'))
      AND NOT EXISTS(SELECT FROM grade later JOIN grade_sheet ls ON ls.sheet_id=later.sheet_id WHERE later.student_id=g.student_id AND ls.item_id=gs.item_id AND ls.stage=gs.stage AND ls.status<>'cancelled' AND (coalesce(ls.exam_date,'infinity'::date),ls.sheet_id)>(gs.exam_date,gs.sheet_id))
      ORDER BY g.grade_id DESC LIMIT 1""").fetchone();assert grade
    correction,_=request('request_grade_correction',{'sheet_number':grade['sheet_number'],'record_book':grade['record_book_number'],'new_points':52 if grade['points']!=52 else 53,'reason':'Проверка собственной заявки'})
    peer=db.execute("""SELECT g.grade_id,g.points,gs.sheet_number,s.record_book_number FROM grade g JOIN grade_sheet gs USING(sheet_id) JOIN student s USING(student_id)
      WHERE s.student_id<>1 AND s.group_id=(SELECT group_id FROM student WHERE student_id=1) AND gs.status='closed'
      AND NOT EXISTS(SELECT FROM grade_correction c WHERE c.grade_id=g.grade_id AND c.status IN('pending','approved'))
      AND NOT EXISTS(SELECT FROM grade later JOIN grade_sheet ls ON ls.sheet_id=later.sheet_id WHERE later.student_id=g.student_id AND ls.item_id=gs.item_id AND ls.stage=gs.stage AND ls.status<>'cancelled' AND (coalesce(ls.exam_date,'infinity'::date),ls.sheet_id)>(gs.exam_date,gs.sheet_id))
      ORDER BY g.grade_id DESC LIMIT 1""").fetchone();assert peer
    peer_correction,_=request('request_grade_correction',{'sheet_number':peer['sheet_number'],'record_book':peer['record_book_number'],'new_points':52 if peer['points']!=52 else 53,'reason':'Заявка одногруппника должна быть скрыта'})
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row)as c:
        c.execute('SELECT backend.set_actor(6,%s)',(token(),));c.commit()
        c.execute('SET LOCAL ROLE deanery_reader')
        rows=c.execute('SELECT c.correction_id,g.student_id FROM deanery.grade_correction c JOIN deanery.grade g USING(grade_id)').fetchall()
        assert any(r['correction_id']==correction['correction_id']for r in rows)
        assert all(r['correction_id']!=peer_correction['correction_id']for r in rows)
        assert all(r['student_id']==1 for r in rows)
        own=c.execute('SELECT correction_id FROM deanery.v_grade_corrections').fetchall()
        assert {r['correction_id']for r in own}=={r['correction_id']for r in rows}
    request('decide_grade_correction',{'correction_id':correction['correction_id'],'approve':False,'comment':'Проверка области доступа завершена'},'DIRECTOR')
    request('decide_grade_correction',{'correction_id':peer_correction['correction_id'],'approve':False,'comment':'Проверка области доступа завершена'},'DIRECTOR')


def test_stale_connection_context_cannot_keep_command(api):
    db,_,_,_,_=api
    uid=db.execute('SELECT user_id FROM app_user WHERE login=%s',(os.environ['TEST_ADMIN_LOGIN'],)).fetchone()['user_id']
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row)as c:
        c.execute('SELECT backend.set_actor(%s,%s)',(uid,token()));c.execute("SELECT backend.command('enroll_student')");c.commit()
        try:
            # Only the test owner can simulate a stale row from a different
            # PostgreSQL process start; runtime cannot modify this table.
            db.execute("UPDATE backend.actor_context SET backend_start=backend_start-interval'1 day' WHERE pid=%s",(c.info.backend_pid,))
            assert c.execute('SELECT backend.actor() actor,backend.current_command() command').fetchone()=={'actor':None,'command':None}
            c.execute("SELECT backend.command('transfer_student')");c.commit()
            assert c.execute('SELECT backend.current_command() command').fetchone()['command'] is None
        finally:db.execute('DELETE FROM backend.actor_context WHERE pid=%s',(c.info.backend_pid,))
