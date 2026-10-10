"""Build the checked-in API contract from the read-only canonical inventory."""
import json,re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
inventory = json.loads(re.search(r'<script type="application/json" id="schema">(.*?)</script>',(root/'sources/deanery_db/schema_explorer.html').read_text(encoding='utf-8-sig'),re.S).group(1))
all_roles = ['admin', 'director', 'dean_staff', 'teacher', 'student']
staff = ['admin', 'director', 'dean_staff']
reference = 'position academic_degree academic_title education_level study_form funding_type student_status control_type lesson_type score_band academic_term holiday pair_time order_type leave_reason contact_relation scholarship_type document_type request_status app_role classroom specialty agent_intent'.split()
admin_only = 'app_user employee teacher dean_office_staff institute department'.split()
workflow = {
 'student': ['enroll_student','expel_student','transfer_student','grant_academic_leave','return_from_leave','reinstate_student'],
 'academic_order': ['enroll_student','expel_student','transfer_student','grant_academic_leave','return_from_leave','reinstate_student','create_order'],
 'order_student': ['create_order'], 'academic_leave':['grant_academic_leave','return_from_leave'],
 'grade_sheet':['create_grade_sheet','update_grade_sheet','close_grade_sheet','cancel_grade_sheet'],
 'grade':['record_grade','remove_grade','request_grade_correction'],
 'grade_correction':['request_grade_correction','decide_grade_correction'], 'app_user':['create_user','update_user','change_password'],
}
view_keys = {
 'v_students':['student_id'], 'v_curriculum':['item_id'], 'v_teachers':['employee_id'], 'v_employees':['employee_id'],
 'v_groups':['group_id'], 'v_schedule':['slot_id'], 'v_schedule_calendar':['slot_id','lesson_date'],
 'v_schedule_hours':['assignment_id'], 'v_grades':['grade_id'], 'v_last_result':['student_id','discipline_id','semester','stage'],
 'v_performance':['student_id','item_id'], 'v_debtors':['student_id','discipline','semester','control_type'],
 'v_expulsion_risk':['student_id'], 'v_student_summary':['student_id'], 'v_student_rating':['student_id'],
 'v_active_scholarships':['scholarship_id'], 'v_teacher_load':['employee_id'], 'v_document_queue':['request_id'],
 'v_attendance_stats':['student_id'], 'v_student_orders':['order_id','student_id'], 'v_academic_leaves':['leave_id'],
 'v_practice':['placement_id'], 'v_academic_works':['work_id'], 'v_student_contacts':['student_id','contact_person_id'],
 'v_audit':['audit_id','column_name'],
 'v_pending_orders':['order_id','student_id'], 'v_grade_corrections':['correction_id'],
}
resources = {}
for t in inventory['tables']:
    name = t['name']
    fields = {c['name']: {'type':c['type'], 'nullable':not c['notnull'], 'identity':c['identity'], 'default':c['default'], 'description':c['comment'], 'relation':c['fk']} for c in t['columns']}
    keys = [c['name'] for c in t['columns'] if c['pk']]
    hidden = {'password_hash','passport_series','passport_number','snils','inn','address','birth_date'} if name in ('person','app_user') else set()
    read = [k for k in fields if k not in hidden]
    write = [k for k,v in fields.items() if not v['identity'] and k not in {'created_at','last_login_at','password_hash','completed_at'}]
    policy = 'reference' if name in reference else name
    writes = ['admin'] if name in reference + admin_only else staff
    reads = all_roles
    ops = ['read','create','update','delete']
    if name in workflow:
        ops = ['read','workflow']
        write = []
    if name == 'student':
        ops += ['update']; write = ['needs_dormitory','is_foreign','funding_type_id']
    if name in ('grade','attendance','grade_correction'):
        writes = staff + ['teacher']
    if name == 'grade_sheet': writes = staff + ['teacher']
    if name == 'document_request': writes = staff + ['student']; ops = ['read','create','update']
    if name in ('audit_log','audit_log_detail'):
        reads = staff; writes = []; write = []; ops = ['read']
    if name in ('app_user','app_role'): reads = ['admin']
    if name == 'agent_request': writes=[];write=[];ops=['read']
    if name in ('contact_person','student_contact'): reads = staff + ['student']
    if name in ('academic_order','order_student','academic_leave','organization','organization_contact','practice_placement','academic_work'):
        reads = staff + ['student'] if name not in ('practice_placement','academic_work') else all_roles
    resources[name] = {'kind':'table','schema':'deanery','key':keys,'fields':fields,'read_fields':read,'write_fields':write,'policy':policy,'read_roles':reads,'write_roles':writes,'operations':ops,'workflow_only':workflow.get(name,[]),'description':t.get('comment','')}
staff_catalogs='scholarship_type document_type request_status order_type leave_reason contact_relation specialty study_program classroom academic_term holiday'.split()
for name,spec in resources.items():
    spec['operation_roles']={op:spec['read_roles'] if op=='read' else spec['write_roles'] for op in spec['operations']}
    if name in staff_catalogs:
        spec['write_roles']=staff
        for op in ('create','update'): spec['operation_roles'][op]=staff
        spec['operation_roles']['delete']=['admin']
for name in ('person','contact_person'):
    resources[name]['operation_roles']['create']=['admin']
    resources[name]['description']+=' Standalone creation: administrator only; deanery staff use enrollment or atomic student-contact command.'
resources['student_contact']['operations'].append('workflow')
resources['document_request']['operation_roles']['update']=staff
resources['student_contact']['operation_roles']['workflow']=staff
resources['student_contact']['workflow_only'].append('create_student_contact')
resources['person']['private_read_fields']=['person_id','passport_series','passport_number','snils','inn','address','birth_date','phone','email']
resources['person']['private_read_roles']=staff+['self']
for column,kind in [('request_id','text'),('agent_run_id','uuid')]:
    resources['audit_log']['fields'][column]={'type':kind,'nullable':True,'identity':False,'default':None,'description':'Verified request correlation','relation':None}
    resources['audit_log']['read_fields'].append(column)
for v in inventory['views']:
    name = v['name']; keys = view_keys[name]
    cols = list(v['columns']) + [k for k in keys if k not in v['columns']]
    cols = [k for k in cols if k not in ('birth_date','phone','email') or name == 'v_student_contacts']
    if name=='v_audit':cols=[c for c in cols if c!='agent_request']+['app_user_id','agent_request_id']
    reads = staff if name == 'v_audit' else staff + ['student'] if name in ('v_student_contacts','v_academic_leaves','v_student_orders','v_pending_orders') else all_roles
    resources[name] = {'kind':'view','schema':'deanery','key':keys,'fields':{c:{'type':'integer' if c.endswith('_id') else 'text','nullable':True} for c in cols},'read_fields':cols,'write_fields':[],'policy':'underlying_rls','read_roles':reads,'write_roles':[],'operations':['read'],'workflow_only':[],'description':v.get('comment','')}
types_path=root/'migrations/view_columns.json'
if types_path.exists():
    for name,columns in json.loads(types_path.read_text(encoding='utf-8')).items():
        for field in resources[name]['fields']:
            resources[name]['fields'][field]=columns.get(field,resources[name]['fields'][field])
resources['v_audit']['policy']='mutation_scope_snapshot'
resources['v_audit']['description']+=' Historical institute scope is captured at mutation; unknown historical scope is administrator-only. Safe structured before/after values are explicitly allowlisted.'
resources['audit_log']['agent_read_fields']=[]
resources['audit_log_detail']['agent_read_fields']=[]
resources['v_audit']['agent_read_fields']=['audit_id','changed_at','table_name','record_key','operation','column_name','app_user_id','agent_request_id','old_value','new_value']
resources['agent_request']['agent_read_fields']=['agent_request_id','user_id','intent_code','status','created_at','execution_ms']
(root/'backend/deanery_api/schema.json').write_text(json.dumps({'version':1,'resources':resources},ensure_ascii=False,indent=2),encoding='utf-8')
