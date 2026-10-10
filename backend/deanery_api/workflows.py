import asyncio
import hashlib
import json
import uuid
from datetime import date,datetime,time,timedelta,timezone
from typing import Literal,Union
from fastapi import APIRouter,Depends,Header,Query,Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel,ConfigDict,Field,ValidationError,StrictInt,StrictBool,model_validator,create_model
from .auth import require_principal,authorise,hasher,verify_password
from .db import transaction
from .errors import ApiError
from .resources import mutate_resource,read_resource,models,read_models
from .schemas import json_value

router=APIRouter(prefix='/workflows',tags=['Domain commands'])


def business_today():
    return datetime.now(timezone(timedelta(hours=3))).date()


class Command(BaseModel):
    model_config=ConfigDict(extra='forbid')


class OrderCommand(Command):
    order_number:str=Field(min_length=1,max_length=30)
    signed_by:StrictInt
    effective_date:date=Field(default_factory=business_today)
    order_date:date|None=None


class Enroll(OrderCommand):
    last_name:str=Field(min_length=1,max_length=60)
    first_name:str=Field(min_length=1,max_length=60)
    middle_name:str|None=Field(default=None,max_length=60)
    gender:str=Field(pattern='^[MF]$')
    birth_date:date
    email:str|None=Field(default=None,max_length=120)
    phone:str|None=Field(default=None,max_length=20)
    group_name:str=Field(min_length=1,max_length=20)
    funding:str=Field(min_length=1,max_length=50)
    record_book:str=Field(min_length=1,max_length=20)


class StudentOrder(OrderCommand):
    student_id:StrictInt=Field(gt=0)


class Expel(StudentOrder):
    reason:str=Field(min_length=1,max_length=300)


class Transfer(StudentOrder):
    group_name:str=Field(min_length=1,max_length=20)


class ReturnFromLeave(StudentOrder):
    group_name:str|None=Field(default=None,min_length=1,max_length=20)


class Leave(StudentOrder):
    reason:str=Field(min_length=1,max_length=100)
    start:date
    end:date


class Sheet(Command):
    sheet_number:str=Field(min_length=1,max_length=20)
    item_id:StrictInt=Field(gt=0)
    group_id:StrictInt=Field(gt=0)
    examiner_id:StrictInt=Field(gt=0)
    stage:str=Field(default='final',pattern='^(module_1|module_2|final)$')
    sheet_kind:str=Field(default='main',pattern='^(main|retake|commission|individual)$')
    issue_date:date=Field(default_factory=business_today)
    exam_date:date|None=None


class SheetState(Command):
    sheet_id:StrictInt=Field(gt=0)
    exam_date:date|None=None
    closed_date:date=Field(default_factory=business_today)


class SheetUpdate(Command):
    sheet_id:StrictInt=Field(gt=0)
    examiner_id:StrictInt|None=Field(default=None,gt=0)
    exam_date:date|None=None

    @model_validator(mode='after')
    def supplied_changes(self):
        if not self.model_fields_set.intersection({'examiner_id','exam_date'}):
            raise ValueError('At least one grade-sheet change is required')
        if 'examiner_id' in self.model_fields_set and self.examiner_id is None:
            raise ValueError('Examiner cannot be null')
        return self


class Grade(Command):
    sheet_id:StrictInt=Field(gt=0)
    student_id:StrictInt=Field(gt=0)
    points:StrictInt|None=Field(default=None,gt=0,le=32767)
    is_absent:StrictBool=False


class CorrectionRequest(Command):
    sheet_number:str=Field(min_length=1,max_length=20)
    record_book:str=Field(min_length=1,max_length=20)
    new_points:StrictInt|None=None
    new_is_absent:StrictBool=False
    reason:str=Field(min_length=1,max_length=500,pattern=r'^\S(?:[\s\S]*\S)?$')


class CorrectionDecision(Command):
    correction_id:StrictInt=Field(gt=0)
    approve:StrictBool
    comment:str|None=Field(default=None,min_length=1,max_length=500,pattern=r'^\S(?:[\s\S]*\S)?$')

    @model_validator(mode='after')
    def rejection_reason(self):
        if not self.approve and not self.comment:
            raise ValueError('A rejection requires a comment')
        return self


class UserCreate(Command):
    person_id:StrictInt=Field(gt=0)
    role_id:StrictInt=Field(gt=0)
    login:str=Field(min_length=1,max_length=50)
    password:str=Field(min_length=12,max_length=1024)


class UserUpdate(Command):
    user_id:StrictInt=Field(gt=0)
    role_id:StrictInt|None=None
    is_active:StrictBool|None=None
    password:str|None=Field(default=None,min_length=12,max_length=1024)


class CreateOrder(OrderCommand):
    student_id:StrictInt=Field(gt=0)
    type_code:str=Field(pattern='^(scholarship|practice|thesis_topics)$')
    title:str=Field(min_length=1,max_length=300)
    reason:str=Field(min_length=1,max_length=300)


ContactDetails=models['contact_person']['create']


class StudentContact(Command):
    student_id:StrictInt=Field(gt=0)
    relation_id:StrictInt=Field(gt=0)
    is_emergency:StrictBool=False
    contact:ContactDetails


class CancelScheduled(Command):
    event_id:uuid.UUID


command_models={'create_student_contact':StudentContact,'enroll_student':Enroll,'expel_student':Expel,'transfer_student':Transfer,'grant_academic_leave':Leave,'return_from_leave':ReturnFromLeave,'reinstate_student':Transfer,'create_order':CreateOrder,'create_grade_sheet':Sheet,'update_grade_sheet':SheetUpdate,'close_grade_sheet':SheetState,'cancel_grade_sheet':SheetState,'record_grade':Grade,'remove_grade':Grade,'request_grade_correction':CorrectionRequest,'decide_grade_correction':CorrectionDecision,'create_user':UserCreate,'update_user':UserUpdate,'cancel_scheduled':CancelScheduled}
student_commands={'enroll_student','expel_student','transfer_student','grant_academic_leave','return_from_leave','reinstate_student','create_order'}


class EnrollmentResult(Command):
    student_id: int


class StudentOrderResult(EnrollmentResult):
    order_id: int
    effective_date: date


class PendingWorkflow(Command):
    event_id: uuid.UUID
    job_id: uuid.UUID
    status: Literal['pending']
    effective_date: date
    student_id: int
    order_id: int


class CancelledWorkflow(Command):
    event_id: uuid.UUID
    status: Literal['cancelled']


class SheetResult(Command):
    sheet_id: int
    status: Literal['open','closed','cancelled']


class SheetUpdateResult(SheetResult):
    examiner_id: int
    exam_date: date | None


class RemovedGrade(Command):
    grade_id: int


class SavedGrade(RemovedGrade):
    sheet_id: int
    student_id: int
    points: int | None
    is_absent: bool


class CorrectionResult(Command):
    correction_id: int
    status: Literal['pending','applied','rejected']


class UserResult(Command):
    user_id: int
    person_id: int
    role_id: int
    login: str
    is_active: bool


ContactRead=read_models['contact_person']


class StudentContactResult(Command):
    contact: ContactRead
    student_id: int
    relation_id: int
    is_emergency: bool


response_models={name:StudentOrderResult|PendingWorkflow for name in student_commands}
response_models.update({'enroll_student':StudentOrderResult|PendingWorkflow,'create_student_contact':StudentContactResult,
    'create_grade_sheet':SheetResult,'update_grade_sheet':SheetUpdateResult,'close_grade_sheet':SheetResult,
    'cancel_grade_sheet':SheetResult,'record_grade':SavedGrade,'remove_grade':RemovedGrade,
    'request_grade_correction':CorrectionResult,'decide_grade_correction':CorrectionResult,'create_user':UserResult,'update_user':UserResult,'cancel_scheduled':CancelledWorkflow})
WorkflowResponse=Union[tuple(dict.fromkeys(response_models.values()))]
ScheduledWorkflowResult=EnrollmentResult|StudentOrderResult|CancelledWorkflow


class ScheduledStatus(Command):
    id: uuid.UUID
    job_id: uuid.UUID | None
    name: Literal['enroll_student','expel_student','transfer_student','grant_academic_leave','return_from_leave','reinstate_student','create_order']
    effective_date: date | None
    status: Literal['pending','applied','cancelled']
    result: EnrollmentResult | StudentOrderResult | None


class ScheduledList(Command):
    items: list[ScheduledStatus]
    limit: int
    offset: int
    has_more: bool


async def one(conn,query,params=()):
    return await(await conn.execute(query,params)).fetchone()


async def enqueue_order(conn,principal,name,result):
    """Attach durable timing to one already-created native order membership."""
    row=await one(conn,'''INSERT INTO backend.domain_events(id,user_id,name,order_id,student_id)
        VALUES(%s,%s,%s,%s,%s) ON CONFLICT(order_id,student_id) DO UPDATE SET id=backend.domain_events.id
        RETURNING id,user_id''',(uuid.uuid4(),principal.user_id,name,result['order_id'],result['student_id']))
    if row['user_id']!=principal.user_id: raise ApiError(409,'already_scheduled','Order already belongs to another scheduled task')
    eid=row['id']
    from .jobs import enqueue
    effective=date.fromisoformat(str(result['effective_date']))
    job=await enqueue(conn,'apply_workflow',{'owner_id':principal.user_id,'event_id':str(eid),'scheduled_at':datetime.combine(effective,time.min,tzinfo=timezone(timedelta(hours=3))).isoformat()},'domain:'+str(eid))
    return {**result,'event_id':str(eid),'job_id':str(job['id']),'status':'pending'}


async def schedule_seed_orders(conn,principal):
    """Explicit synthetic initialization only; the bootstrap admin owns demo jobs."""
    if 'admin' not in principal.roles: raise ApiError(403,'forbidden','Bootstrap administrator required')
    names={'enroll':'enroll_student','expel':'expel_student','transfer':'transfer_student','leave':'grant_academic_leave','leave_return':'return_from_leave','reinstate':'reinstate_student'}
    rows=await(await conn.execute('''SELECT os.order_id,os.student_id,t.code,fn_order_due_date(os.order_id,os.student_id) AS effective_date
        FROM deanery.order_student os JOIN deanery.academic_order o USING(order_id) JOIN deanery.order_type t USING(order_type_id)
        WHERE os.applied_at IS NULL AND NOT EXISTS(SELECT 1 FROM backend.domain_events e WHERE e.order_id=os.order_id AND e.student_id=os.student_id)
        ORDER BY os.order_id,os.student_id''')).fetchall()
    results=[]
    for row in rows:
        name=names[row.pop('code')]
        results.append(await enqueue_order(conn,principal,name,row))
    return results


async def run_workflow(conn,principal,name,values,idempotency_key):
    if name not in command_models: raise ApiError(404,'unknown_workflow','Workflow not found')
    if not idempotency_key or len(idempotency_key)>100: raise ApiError(422,'idempotency_required','Idempotency-Key of 1..100 characters required')
    try: data=command_models[name].model_validate(values).model_dump(exclude_unset=name=='update_grade_sheet')
    except ValidationError as exc: raise ApiError(422,'validation_error','Invalid command',[{'location':list(e['loc']),'type':e['type']} for e in exc.errors()]) from None
    resource='grade_correction' if name in ('request_grade_correction','decide_grade_correction') else 'student_contact' if name=='create_student_contact' else 'app_user' if name.endswith('_user') else 'student' if name in student_commands or name=='cancel_scheduled' else 'grade_sheet' if name.endswith('grade_sheet') else 'grade'
    await authorise(conn,principal,'workflow',resource)
    payload_hash=hashlib.sha256(json.dumps(json_value(data),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    previous=await one(conn,'SELECT * FROM backend.command_results WHERE user_id=%s AND key=%s FOR UPDATE',(principal.user_id,idempotency_key))
    if previous:
        if previous['payload_hash']!=payload_hash or previous['name']!=name: raise ApiError(409,'idempotency_conflict','Key already used for another command')
        return previous['result']
    result=await execute_command(conn,principal,name,data)
    if name in student_commands:
        pending=result.pop('pending',False)
        if pending:
            result=await enqueue_order(conn,principal,name,result)
    await conn.execute('INSERT INTO backend.command_results(user_id,key,name,payload_hash,result) VALUES(%s,%s,%s,%s,%s)',(principal.user_id,idempotency_key,name,payload_hash,Jsonb(json_value(result))))
    return result


async def apply_scheduled(conn,principal,event_id):
    await authorise(conn,principal,'workflow','student')
    result=await one(conn,'SELECT backend.apply_scheduled_order(%s) AS result',(uuid.UUID(str(event_id)),))
    return result['result']


async def execute_command(conn,principal,name,data):
    await conn.execute('SELECT backend.command(%s)',(name,))
    if name=='create_student_contact':
        result=await one(conn,'SELECT backend.create_student_contact(%s,%s,%s,%s) AS result',(data['student_id'],Jsonb(data['contact']),data['relation_id'],data['is_emergency']))
        return result['result']
    if name=='cancel_scheduled':
        result=await one(conn,'SELECT backend.cancel_scheduled_order(%s) AS result',(data['event_id'],))
        return result['result']
    if name in student_commands:
        result=await one(conn,'SELECT backend.student_workflow(%s,%s) AS result',(name,Jsonb(json_value(data))))
        return result['result']
    if name=='request_grade_correction':
        result=await one(conn,'SELECT backend.request_grade_correction(%s,%s,%s,%s,%s) AS correction_id',
            (data['sheet_number'],data['record_book'],data['new_points'],data['new_is_absent'],data['reason']))
        return {**result,'status':'pending'}
    if name=='decide_grade_correction':
        result=await one(conn,'SELECT backend.decide_grade_correction(%s,%s,%s) AS status',
            (data['correction_id'],data['approve'],data['comment']))
        return {'correction_id':data['correction_id'],**result}
    if name.endswith('_user'):
        if 'admin' not in principal.roles: raise ApiError(403,'forbidden','Administrator required')
        password=data.pop('password',None)
        encoded=await asyncio.to_thread(hasher.hash,password) if password else None
        if name=='create_user':
            return await one(conn,'INSERT INTO deanery.app_user(person_id,role_id,login,password_hash) VALUES(%s,%s,%s,%s) RETURNING user_id,person_id,role_id,login,is_active',(data['person_id'],data['role_id'],data['login'],encoded))
        uid=data['user_id']
        row=await one(conn,'UPDATE deanery.app_user SET role_id=coalesce(%s,role_id),is_active=coalesce(%s,is_active),password_hash=coalesce(%s,password_hash) WHERE user_id=%s RETURNING user_id,person_id,role_id,login,is_active',(data['role_id'],data['is_active'],encoded,uid))
        if not row: raise ApiError(404,'not_found','User not found')
        await conn.execute('UPDATE backend.sessions SET revoked_at=now() WHERE user_id=%s',(uid,))
        return row
    if name=='create_grade_sheet':
        if 'teacher' in principal.roles: raise ApiError(403,'forbidden','Deanery creates grade sheets')
        from psycopg import sql
        row=await one(conn,sql.SQL('INSERT INTO deanery.grade_sheet({}) VALUES({}) RETURNING sheet_id,status').format(sql.SQL(',').join(map(sql.Identifier,data)),sql.SQL(',').join(sql.Placeholder() for _ in data)),tuple(data.values()))
        return row
    sheet=await one(conn,'SELECT * FROM deanery.grade_sheet WHERE sheet_id=%s FOR UPDATE',(data['sheet_id'],))
    if not sheet: raise ApiError(404,'not_found','Grade sheet not found')
    if name=='update_grade_sheet':
        if not principal.roles.intersection({'admin','director','dean_staff'}): raise ApiError(403,'forbidden','Deanery updates grade sheets')
        if sheet['status']!='open': raise ApiError(409,'invalid_state','Only open grade sheets can be updated')
        from psycopg import sql
        changes={k:v for k,v in data.items() if k!='sheet_id'}
        return await one(conn,sql.SQL('UPDATE deanery.grade_sheet SET {} WHERE sheet_id=%s RETURNING sheet_id,examiner_id,exam_date,status').format(sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in changes)),(*changes.values(),data['sheet_id']))
    if 'teacher' in principal.roles and sheet['examiner_id']!=principal.teacher_id: raise ApiError(403,'forbidden','Only assigned examiner may change results')
    if name in ('close_grade_sheet','cancel_grade_sheet'):
        return await one(conn,'UPDATE deanery.grade_sheet SET status=%s,closed_date=%s,exam_date=coalesce(%s,exam_date) WHERE sheet_id=%s RETURNING sheet_id,status',('closed' if name=='close_grade_sheet' else 'cancelled',data['closed_date'] if name=='close_grade_sheet' else None,data['exam_date'],data['sheet_id']))
    if name=='remove_grade':
        row=await one(conn,'DELETE FROM deanery.grade WHERE sheet_id=%s AND student_id=%s RETURNING grade_id',(data['sheet_id'],data['student_id']))
        if not row: raise ApiError(404,'not_found','Grade not found')
        return row
    return await one(conn,'INSERT INTO deanery.grade(sheet_id,student_id,points,is_absent) VALUES(%s,%s,%s,%s) ON CONFLICT(sheet_id,student_id) DO UPDATE SET points=excluded.points,is_absent=excluded.is_absent RETURNING grade_id,sheet_id,student_id,points,is_absent',(data['sheet_id'],data['student_id'],data['points'],data['is_absent']))


def register(name,model):
    async def command(payload:model,request:Request,idempotency_key:str=Header(alias='Idempotency-Key'),principal=Depends(require_principal)):
        async with transaction(principal,request.state.request_id) as conn:
            return json_value(await run_workflow(conn,principal,name,payload.model_dump(exclude_unset=True),idempotency_key))
    router.add_api_route('/'+name,command,methods=['POST'],name=name,responses={200:{'model':response_models[name]}})

for _name,_model in command_models.items(): register(_name,_model)


SCHEDULED_SELECT = """SELECT e.id,j.id AS job_id,e.name,o.effective_date,e.created_at,
 CASE WHEN e.cancelled_at IS NOT NULL THEN 'cancelled' WHEN os.applied_at IS NOT NULL THEN 'applied' ELSE 'pending' END AS status,
 CASE WHEN os.applied_at IS NOT NULL THEN jsonb_build_object('student_id',e.student_id,'order_id',e.order_id,'effective_date',o.effective_date) END AS result
 FROM backend.domain_events e LEFT JOIN deanery.academic_order o ON o.order_id=e.order_id
 LEFT JOIN deanery.order_student os ON os.order_id=e.order_id AND os.student_id=e.student_id
 LEFT JOIN backend.jobs j ON j.owner_id=e.user_id AND j.kind='apply_workflow' AND j.idempotency_key='domain:'||e.id::text
 WHERE e.user_id=%s"""


@router.get('/scheduled', response_model=ScheduledList)
async def scheduled_list(request:Request, limit:int=Query(30,ge=1,le=100), offset:int=Query(0,ge=0,le=100000),
                         status:Literal['pending','applied','cancelled']|None=None,principal=Depends(require_principal)):
    async with transaction(principal,request.state.request_id,readonly=True) as conn:
        rows=await(await conn.execute('SELECT id,job_id,name,effective_date,status,result FROM ('+SCHEDULED_SELECT+
            ') scheduled WHERE (%s::text IS NULL OR status=%s) ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s',
            (principal.user_id,status,status,limit+1,offset))).fetchall()
        return {'items':json_value(rows[:limit]),'limit':limit,'offset':offset,'has_more':len(rows)>limit}


@router.get('/scheduled/{event_id}',responses={200:{'model':ScheduledStatus}})
async def scheduled_status(event_id:uuid.UUID,request:Request,principal=Depends(require_principal)):
    async with transaction(principal,request.state.request_id,readonly=True) as conn:
        result=await one(conn,'SELECT id,job_id,name,effective_date,status,result FROM ('+SCHEDULED_SELECT+
            ') scheduled WHERE id=%s',(principal.user_id,event_id))
        if not result: raise ApiError(404,'not_found','Scheduled command not found')
        return json_value(result)
