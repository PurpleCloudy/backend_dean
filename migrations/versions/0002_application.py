"""Application auth, scoped access, audit and durable jobs over the canonical schema."""
import json,re
from pathlib import Path
from alembic import op
from procrastinate.schema import SchemaManager

revision='0002'
down_revision='0001'


def upgrade():
    root=Path(__file__).resolve().parents[2]
    conn=op.get_bind()
    def script(name):
        conn.exec_driver_sql((root/'migrations'/name).read_text(encoding='utf-8'))
    script('roles.sql')
    script('chat.sql')
    script('application.sql')
    script('domain_security.sql')
    script('commands.sql')
    conn.exec_driver_sql("SET search_path=deanery,backend,public")
    source=(root/'sources/deanery_db/02_views_functions.sql').read_text(encoding='utf-8-sig')
    # Keep source business semantics, adding explicit stable keys and RLS-safe projections.
    extras={'v_curriculum':'ci.item_id','v_schedule':'ss.slot_id','v_schedule_hours':'ta.assignment_id',
      'v_grades':'gr.grade_id','v_performance':'ci.item_id','v_active_scholarships':'sc.scholarship_id',
      'v_teacher_load':'e.employee_id','v_attendance_stats':'s.student_id','v_student_orders':'o.order_id',
      'v_pending_orders':'os.order_id','v_academic_leaves':'al.leave_id','v_practice':'pp.placement_id',
      'v_academic_works':'aw.work_id','v_student_contacts':'s.student_id, cp.contact_person_id'}
    for name,body in re.findall(r'CREATE OR REPLACE VIEW (\w+) AS\n(.*?);',source,re.S):
        if name=='v_audit': continue  # Scoped barrier view; raw journal remains inaccessible to the SQL reader.
        body=re.sub(r'fn_full_name\((\w+)\)',r"concat_ws(' ', \1.last_name, \1.first_name, \1.middle_name)",body)
        if name in extras: body=body.replace('\nFROM ', ', '+extras[name]+'\nFROM ',1)
        if name=='v_students': body=body.replace('p.birth_date','NULL::date AS birth_date')
        if name=='v_teacher_load': body=body.replace('GROUP BY p.person_id, d.short_name','GROUP BY p.person_id, d.short_name, e.employee_id')
        if name=='v_attendance_stats': body=body.replace('GROUP BY p.person_id, g.name','GROUP BY p.person_id, g.name, s.student_id')
        if name=='v_grade_corrections':
            body=body.replace('ru.login','backend.correction_user_label(gc.requested_by_id)::varchar(50)').replace('du.login','backend.correction_user_label(gc.decided_by_id)::varchar(50)')
            body=re.sub(r'\n(?:LEFT )?JOIN app_user[^\n]+','',body)
        conn.exec_driver_sql(f'CREATE OR REPLACE VIEW deanery.{name} WITH(security_invoker=true) AS '+body)
    script('schedule_report.sql')
    # Retakes across equivalent curriculum years retain the previously supported discipline/semester key.
    grade=conn.exec_driver_sql("SELECT pg_get_functiondef('deanery.trg_grade_check()'::regprocedure)").scalar()
    grade=grade.replace('gs.item_id = v_sheet.item_id','(SELECT (discipline_id,semester) FROM curriculum_item WHERE item_id=gs.item_id) = (SELECT (discipline_id,semester) FROM curriculum_item WHERE item_id=v_sheet.item_id)')
    conn.exec_driver_sql(grade)
    conn.exec_driver_sql("""
ALTER TABLE deanery.teaching_assignment ADD CONSTRAINT assignment_unique_nulls UNIQUE NULLS NOT DISTINCT(item_id,group_id,lesson_type_id,subgroup);
ALTER TABLE deanery.grade_correction ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON deanery.grade_correction FOR SELECT USING(backend.allowed('grade_correction',to_jsonb(grade_correction),false));
DO $$ DECLARE t record; f record; BEGIN
 FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='deanery' AND tablename NOT IN('audit_log','audit_log_detail') LOOP
  EXECUTE format('DROP TRIGGER IF EXISTS audit ON deanery.%I',t.tablename);
  EXECUTE format('CREATE TRIGGER audit AFTER INSERT OR UPDATE ON deanery.%I FOR EACH ROW EXECUTE FUNCTION deanery.trg_audit()',t.tablename);
  EXECUTE format('CREATE TRIGGER audit_delete BEFORE DELETE ON deanery.%I FOR EACH ROW EXECUTE FUNCTION deanery.trg_audit()',t.tablename);
 END LOOP;
 FOR f IN SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='deanery' AND (p.proname LIKE 'trg_%' OR p.proname IN('fn_check_slot','fn_mark','fn_result_name')) LOOP
  EXECUTE format('ALTER FUNCTION %s SECURITY DEFINER',f.name);
 END LOOP;
 FOR f IN SELECT p.oid::regprocedure AS name,p.prokind FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN('deanery','backend') AND p.prosecdef LOOP
  EXECUTE format('ALTER %s %s SET search_path=pg_catalog,deanery,backend,pg_temp',CASE WHEN f.prokind='p' THEN 'PROCEDURE' ELSE 'FUNCTION' END,f.name);
 END LOOP;
 EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC,deanery_runtime,deanery_jobs,deanery_reader,deanery_upstream',current_database());
END $$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA deanery FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA deanery FROM PUBLIC;
REVOKE EXECUTE ON ALL PROCEDURES IN SCHEMA deanery FROM PUBLIC;
DO $$ DECLARE role_name text; BEGIN
 FOREACH role_name IN ARRAY ARRAY['deanery_read','deanery_teacher','deanery_staff','deanery_agent'] LOOP
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
   EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA deanery FROM %I',role_name);
   EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA deanery FROM %I',role_name);
   EXECUTE format('REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA deanery FROM %I',role_name);
   EXECUTE format('REVOKE EXECUTE ON ALL PROCEDURES IN SCHEMA deanery FROM %I',role_name);
   EXECUTE format('REVOKE ALL ON SCHEMA deanery FROM %I',role_name);
  END IF;
 END LOOP;
END $$;
GRANT USAGE ON SCHEMA deanery,backend TO deanery_runtime,deanery_jobs,deanery_reader;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA deanery TO deanery_runtime,deanery_jobs;
REVOKE INSERT,UPDATE,DELETE ON deanery.audit_log,deanery.audit_log_detail,deanery.grade_correction FROM deanery_runtime,deanery_jobs;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA deanery TO deanery_runtime,deanery_jobs;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.chat_sessions,public.chat_messages,public.sql_change_proposals TO deanery_runtime,deanery_jobs;
GRANT USAGE,SELECT ON SEQUENCE public.chat_messages_id_seq TO deanery_runtime,deanery_jobs,deanery_upstream;
GRANT SELECT ON public.chat_sessions,public.sql_change_proposals TO deanery_upstream;
GRANT SELECT,INSERT ON public.chat_messages TO deanery_upstream;
GRANT SELECT ON public.alembic_version TO deanery_runtime,deanery_jobs;
ALTER DEFAULT PRIVILEGES IN SCHEMA backend REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA deanery REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
""")
    manifest=json.loads((root/'backend/deanery_api/schema.json').read_text(encoding='utf-8'))
    for name,spec in manifest['resources'].items():
        fields=spec.get('agent_read_fields',spec['read_fields'])
        if fields:
            conn.exec_driver_sql('GRANT SELECT('+','.join('"'+f+'"' for f in fields)+') ON deanery."'+name+'" TO deanery_reader')
    conn.exec_driver_sql("""GRANT EXECUTE ON FUNCTION
deanery.fn_course(integer,date),deanery.fn_semester(integer,date),deanery.fn_credits(deanery.curriculum_item),
deanery.fn_slot_period(deanery.schedule_slot),deanery.fn_week_number(date,date),deanery.fn_mark(integer),
deanery.fn_result_name(integer,boolean,boolean,boolean),deanery.fn_result_cell(integer,boolean),
deanery.fn_group_prefix(integer),deanery.fn_group_institute(integer),deanery.fn_leave_actual_end(deanery.academic_leave),
deanery.fn_order_due_date(integer,integer) TO deanery_runtime,deanery_jobs,deanery_reader""")
    conn.exec_driver_sql('SET search_path=public')
    conn.exec_driver_sql(SchemaManager.get_schema())
    conn.exec_driver_sql("""DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'procrastinate_%' LOOP
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC',r.tablename);
  EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON public.%I TO deanery_runtime,deanery_jobs',r.tablename);
 END LOOP;
 FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname='public' AND sequencename LIKE 'procrastinate_%' LOOP
  EXECUTE format('GRANT USAGE,SELECT ON public.%I TO deanery_runtime,deanery_jobs',r.sequencename);
 END LOOP;
 FOR r IN SELECT p.oid::regprocedure AS fn FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'procrastinate_%' LOOP
  EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC',r.fn);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO deanery_runtime,deanery_jobs',r.fn);
 END LOOP;
END $$""")


def downgrade():
    raise RuntimeError('Use a clean database for this unreleased project baseline')
