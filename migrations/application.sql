SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET client_min_messages = warning;

CREATE SCHEMA backend;

CREATE FUNCTION backend.actor() RETURNS integer
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$ SELECT c.user_id FROM backend.actor_context c JOIN deanery.app_user u ON u.user_id=c.user_id AND u.is_active JOIN pg_stat_activity a ON a.pid=c.pid AND a.backend_start=c.backend_start WHERE c.pid=pg_backend_pid() $$;

CREATE FUNCTION backend.agent_run_identity_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'backend', 'pg_temp'
    AS $$
BEGIN
 IF (NEW.id,NEW.user_id,NEW.session_id,NEW.canonical_request_id,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.user_id,OLD.session_id,OLD.canonical_request_id,OLD.created_at)
 THEN RAISE EXCEPTION 'Agent run identity is immutable' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION backend.allowed(rel text, r jsonb, writing boolean) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$
DECLARE i record; sid integer; gid integer; eid integer; iid integer;
BEGIN
 SELECT * INTO i FROM backend.identity(backend.actor());
 IF i.user_id IS NULL OR NOT i.is_active THEN RETURN false; END IF;
 IF i.role_code='admin' THEN RETURN true; END IF;
 IF rel IN('audit_log','audit_log_detail','app_user','app_role') THEN RETURN false; END IF;
 IF rel IN('scholarship_type','document_type','request_status','order_type','leave_reason','contact_relation','specialty','study_program','classroom','academic_term','holiday') AND i.role_code IN('dean_staff','director') THEN RETURN true; END IF;
 IF rel IN('position','academic_degree','academic_title','education_level','study_form','funding_type','student_status','control_type','lesson_type','score_band','academic_term','holiday','pair_time','order_type','leave_reason','contact_relation','scholarship_type','document_type','request_status','classroom','specialty','agent_intent','curriculum','curriculum_item','study_program','discipline') THEN RETURN NOT writing OR (rel IN('curriculum','curriculum_item','study_program','discipline') AND i.role_code IN('dean_staff','director') AND CASE rel WHEN 'study_program' THEN (SELECT institute_id FROM deanery.department WHERE department_id=(r->>'department_id')::int)=ANY(i.institute_ids) WHEN 'discipline' THEN (SELECT institute_id FROM deanery.department WHERE department_id=(r->>'department_id')::int)=ANY(i.institute_ids) WHEN 'curriculum' THEN (SELECT d.institute_id FROM deanery.study_program p JOIN deanery.department d USING(department_id) WHERE p.program_id=(r->>'program_id')::int)=ANY(i.institute_ids) ELSE (SELECT d.institute_id FROM deanery.curriculum c JOIN deanery.study_program p USING(program_id) JOIN deanery.department d USING(department_id) WHERE c.curriculum_id=(r->>'curriculum_id')::int)=ANY(i.institute_ids) END); END IF;
 IF rel IN('institute','department','teacher','employee','dean_office_staff') THEN RETURN NOT writing; END IF;
 IF rel='grade_correction' THEN RETURN NOT writing AND EXISTS(SELECT 1 FROM deanery.grade gr JOIN deanery.grade_sheet gs USING(sheet_id) WHERE gr.grade_id=(r->>'grade_id')::int AND ((i.role_code='student' AND gr.student_id=i.student_id) OR (i.role_code<>'student' AND (backend.group_allowed(gs.group_id,false) OR backend.student_allowed(gr.student_id,false) OR (i.role_code='teacher' AND gs.examiner_id=i.teacher_id))))); END IF;
 IF rel='student' THEN RETURN backend.student_allowed((r->>'student_id')::int,writing) OR (writing AND backend.current_command()='enroll_student' AND backend.group_allowed((r->>'group_id')::int,true)); END IF;
 IF rel='study_group' THEN RETURN backend.group_allowed((r->>'group_id')::int,writing) OR (writing AND i.role_code IN('dean_staff','director') AND (SELECT d.institute_id FROM deanery.curriculum c JOIN deanery.study_program p USING(program_id) JOIN deanery.department d USING(department_id) WHERE c.curriculum_id=(r->>'curriculum_id')::int)=ANY(i.institute_ids)); END IF;
 IF rel='person' THEN RETURN (NOT writing AND (r->>'person_id')::int=i.person_id) OR EXISTS(SELECT 1 FROM deanery.student s WHERE s.person_id=(r->>'person_id')::int AND backend.student_allowed(s.student_id,writing)) OR (NOT writing AND EXISTS(SELECT 1 FROM deanery.employee e WHERE e.person_id=(r->>'person_id')::int)) OR (writing AND i.role_code IN('dean_staff','director') AND backend.current_command()='enroll_student'); END IF;
 IF rel='teaching_assignment' THEN RETURN backend.group_allowed((r->>'group_id')::int,writing); END IF;
 IF rel='schedule_slot' THEN RETURN EXISTS(SELECT 1 FROM deanery.teaching_assignment a WHERE a.assignment_id=(r->>'assignment_id')::int AND backend.group_allowed(a.group_id,writing)); END IF;
 IF rel='grade_sheet' THEN RETURN backend.group_allowed((r->>'group_id')::int,writing) OR (i.role_code='teacher' AND (r->>'examiner_id')::int=i.teacher_id); END IF;
 IF rel='grade' THEN RETURN EXISTS(SELECT 1 FROM deanery.grade_sheet g WHERE g.sheet_id=(r->>'sheet_id')::int AND (backend.student_allowed((r->>'student_id')::int,writing) OR (i.role_code='teacher' AND g.examiner_id=i.teacher_id))); END IF;
 IF rel='attendance' THEN RETURN backend.student_allowed((r->>'student_id')::int,writing) OR (i.role_code='teacher' AND EXISTS(SELECT 1 FROM deanery.schedule_slot ss JOIN deanery.teaching_assignment a USING(assignment_id) WHERE ss.slot_id=(r->>'slot_id')::int AND a.teacher_id=i.teacher_id)); END IF;
 IF rel='document_request' AND i.role_code='student' THEN RETURN (r->>'student_id')::int=i.student_id; END IF;
 IF rel IN('scholarship','academic_leave','practice_placement','academic_work','document_request','student_contact','order_student') THEN RETURN backend.student_allowed((r->>'student_id')::int,writing); END IF;
 IF rel='academic_order' THEN RETURN (i.role_code IN('dean_staff','director') AND (r->>'institute_id')::int=ANY(i.institute_ids)) OR (NOT writing AND EXISTS(SELECT 1 FROM deanery.order_student os WHERE os.order_id=(r->>'order_id')::int AND os.student_id=i.student_id)) OR (NOT writing AND i.role_code='teacher' AND EXISTS(SELECT 1 FROM deanery.scholarship sc WHERE sc.order_id=(r->>'order_id')::int AND backend.student_allowed(sc.student_id,false))); END IF;
 IF rel='contact_person' THEN RETURN EXISTS(SELECT 1 FROM deanery.student_contact sc WHERE sc.contact_person_id=(r->>'contact_person_id')::int AND backend.student_allowed(sc.student_id,writing)) OR (writing AND i.role_code IN('dean_staff','director')); END IF;
 IF rel IN('organization','organization_contact') THEN RETURN i.role_code IN('dean_staff','director') OR (NOT writing AND EXISTS(SELECT 1 FROM deanery.practice_placement pp JOIN deanery.organization_contact oc USING(org_contact_id) WHERE pp.student_id=i.student_id AND CASE rel WHEN 'organization' THEN oc.organization_id=(r->>'organization_id')::int ELSE oc.org_contact_id=(r->>'org_contact_id')::int END)); END IF;
 IF rel='agent_request' THEN RETURN NOT writing AND (r->>'user_id')::int=i.user_id; END IF;
 RETURN false;
END $$;

CREATE FUNCTION backend.audit_actor() RETURNS integer
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'backend', 'deanery', 'pg_temp'
    AS $$
 SELECT coalesce(backend.actor(),(
  SELECT r.user_id FROM backend.actor_context c
  JOIN pg_stat_activity p ON p.pid=c.pid AND p.backend_start=c.backend_start
  JOIN backend.agent_runs r ON r.id=c.agent_run_id AND r.user_id=c.user_id
  WHERE c.pid=pg_backend_pid()))
$$;

CREATE FUNCTION backend.audit_agent_request() RETURNS integer
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'backend', 'deanery', 'pg_temp'
    AS $$
 SELECT canonical_request_id FROM backend.agent_runs
 WHERE id=backend.audit_run()
$$;

CREATE FUNCTION backend.audit_allowed(institutes integer[], global_scope boolean) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'backend', 'deanery', 'pg_temp'
    AS $$
 SELECT coalesce((SELECT role_code='admin' OR (role_code IN('dean_staff','director') AND
 (global_scope OR institute_ids && institutes)) FROM backend.identity(backend.actor()) WHERE is_active),false)
$$;

CREATE FUNCTION backend.audit_global(rel text) RETURNS boolean
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'pg_catalog', 'pg_temp'
    AS $$
 SELECT rel=ANY(ARRAY['position','academic_degree','academic_title','education_level','study_form',
 'funding_type','student_status','control_type','lesson_type','score_band','academic_term','holiday',
 'pair_time','order_type','leave_reason','contact_relation','scholarship_type','document_type',
 'request_status','classroom','specialty'])
$$;

CREATE FUNCTION backend.audit_institutes(rel text, r jsonb, inserting boolean) RETURNS integer[]
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'pg_temp'
    AS $$
DECLARE result integer[]; pid integer; eid integer; gid integer; iid integer;
BEGIN
 IF r IS NULL THEN RETURN ARRAY[]::integer[]; END IF;
 IF rel='student' OR rel IN('grade_sheet','teaching_assignment') THEN
  SELECT ARRAY[deanery.fn_group_institute((r->>'group_id')::integer)] INTO result;
 ELSIF rel IN('grade','attendance','student_contact','scholarship','academic_leave','practice_placement','academic_work','document_request','order_student') THEN
  SELECT ARRAY[deanery.fn_group_institute(s.group_id)] INTO result FROM deanery.student s WHERE s.student_id=(r->>'student_id')::integer;
  IF rel='order_student' AND r->>'new_group_id' IS NOT NULL THEN result:=coalesce(result,ARRAY[]::integer[])||deanery.fn_group_institute((r->>'new_group_id')::integer); END IF;
 ELSIF rel='grade_correction' THEN
  SELECT ARRAY[deanery.fn_group_institute(gs.group_id)] INTO result FROM deanery.grade g JOIN deanery.grade_sheet gs USING(sheet_id) WHERE g.grade_id=(r->>'grade_id')::integer;
 ELSIF rel='study_group' OR rel='curriculum_item' THEN
  SELECT ARRAY[d.institute_id] INTO result FROM deanery.curriculum c JOIN deanery.study_program p USING(program_id) JOIN deanery.department d USING(department_id) WHERE c.curriculum_id=(r->>'curriculum_id')::integer;
 ELSIF rel='curriculum' THEN
  SELECT ARRAY[d.institute_id] INTO result FROM deanery.study_program p JOIN deanery.department d USING(department_id) WHERE p.program_id=(r->>'program_id')::integer;
 ELSIF rel IN('study_program','discipline','teacher') THEN
  SELECT ARRAY[d.institute_id] INTO result FROM deanery.department d WHERE d.department_id=(r->>'department_id')::integer;
 ELSIF rel IN('department','dean_office_staff','academic_order','institute') THEN
  result:=ARRAY[(r->>'institute_id')::integer];
 ELSIF rel='schedule_slot' THEN
  SELECT ARRAY[deanery.fn_group_institute(a.group_id)] INTO result FROM deanery.teaching_assignment a WHERE a.assignment_id=(r->>'assignment_id')::integer;
 ELSIF rel='employee' THEN
  eid:=(r->>'employee_id')::integer;
  SELECT array_agg(DISTINCT q.institute_id) INTO result FROM (
   SELECT d.institute_id FROM deanery.teacher t JOIN deanery.department d USING(department_id) WHERE t.employee_id=eid
   UNION SELECT s.institute_id FROM deanery.dean_office_staff s WHERE s.employee_id=eid
   UNION SELECT i.institute_id FROM deanery.institute i WHERE i.director_id=eid) q;
 ELSIF rel='person' THEN
  pid:=(r->>'person_id')::integer;
  SELECT array_agg(DISTINCT q.institute_id) INTO result FROM (
   SELECT deanery.fn_group_institute(s.group_id) AS institute_id FROM deanery.student s WHERE s.person_id=pid
   UNION SELECT unnest(backend.audit_institutes('employee',to_jsonb(e),false)) FROM deanery.employee e WHERE e.person_id=pid) q;
 ELSIF rel='contact_person' THEN
  SELECT array_agg(DISTINCT deanery.fn_group_institute(s.group_id)) INTO result FROM deanery.student_contact c JOIN deanery.student s USING(student_id) WHERE c.contact_person_id=(r->>'contact_person_id')::integer;
 ELSIF rel IN('organization','organization_contact') THEN
  SELECT array_agg(DISTINCT deanery.fn_group_institute(s.group_id)) INTO result FROM deanery.organization_contact c JOIN deanery.practice_placement p USING(org_contact_id) JOIN deanery.student s USING(student_id)
  WHERE CASE rel WHEN 'organization' THEN c.organization_id=(r->>'organization_id')::integer ELSE c.org_contact_id=(r->>'org_contact_id')::integer END;
 ELSIF rel='agent_request' THEN
  SELECT backend.audit_institutes('person',jsonb_build_object('person_id',u.person_id),false) INTO result FROM deanery.app_user u WHERE u.user_id=(r->>'user_id')::integer;
 END IF;
 -- Exact temporary scope exists only inside checked atomic enrollment/contact.
 IF inserting AND rel IN('person','contact_person') THEN
  SELECT coalesce(result,ARRAY[]::integer[])||coalesce(c.audit_institute_ids,ARRAY[]::integer[]) INTO result
  FROM backend.actor_context c JOIN pg_stat_activity a ON a.pid=c.pid AND a.backend_start=c.backend_start
  WHERE c.pid=pg_backend_pid() AND c.user_id=backend.actor();
 END IF;
 RETURN ARRAY(SELECT DISTINCT value FROM unnest(coalesce(result,ARRAY[]::integer[])) value WHERE value IS NOT NULL ORDER BY value);
END $$;

CREATE FUNCTION backend.audit_request() RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$ SELECT c.request_id FROM backend.actor_context c JOIN pg_stat_activity a ON a.pid=c.pid AND a.backend_start=c.backend_start WHERE c.pid=pg_backend_pid() $$;

CREATE FUNCTION backend.audit_run() RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$ SELECT coalesce(c.agent_run_id,a.id) FROM backend.actor_context c LEFT JOIN backend.agent_runs a ON a.user_id=c.user_id AND (a.id::text=c.request_id OR a.upstream_run_id::text=c.request_id) WHERE c.pid=pg_backend_pid() AND c.backend_start=(SELECT backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid()) $$;

CREATE FUNCTION backend.change_password(uid integer, encoded text) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$ BEGIN IF uid IS DISTINCT FROM backend.actor() THEN RAISE EXCEPTION 'Password owner mismatch' USING ERRCODE='42501'; END IF; UPDATE deanery.app_user SET password_hash=encoded WHERE user_id=uid; END $$;

CREATE FUNCTION backend.command(cmd text) RETURNS void
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$ UPDATE backend.actor_context SET command=cmd WHERE pid=pg_backend_pid() AND backend_start=(SELECT backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid()) $$;

CREATE FUNCTION backend.contact_parent_scope() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'pg_temp'
    AS $$
BEGIN
 -- Only the actual relation owner may create the first link in the already
 -- scoped SECURITY DEFINER create_student_contact helper. Membership, role
 -- spelling, session_user and attacker-controlled settings grant no bypass.
 IF current_user::regrole::oid=(SELECT relowner FROM pg_class WHERE oid=TG_RELID) THEN
  RETURN NEW;
 END IF;
 IF NOT coalesce(backend.allowed('contact_person',jsonb_build_object('contact_person_id',NEW.contact_person_id),false),false) THEN
  RAISE EXCEPTION 'Contact is not visible before association' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION backend.create_student_contact(sid integer, details jsonb, relation integer, emergency boolean) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'backend', 'deanery', 'pg_temp'
    AS $$
DECLARE contact deanery.contact_person; saved integer[]; inst integer;
BEGIN
 -- Hold the authorized student stable until its first contact link commits.
 PERFORM 1 FROM deanery.student WHERE student_id=sid FOR UPDATE;
 IF NOT coalesce(backend.student_allowed(sid,true),false) OR NOT EXISTS(SELECT 1 FROM backend.identity(backend.actor()) WHERE role_code IN('admin','director','dean_staff') AND is_active) THEN RAISE EXCEPTION 'Student outside your institute' USING ERRCODE='42501'; END IF;
 SELECT deanery.fn_group_institute(group_id) INTO inst FROM deanery.student WHERE student_id=sid;
 SELECT audit_institute_ids INTO saved FROM backend.actor_context WHERE pid=pg_backend_pid();
 UPDATE backend.actor_context SET audit_institute_ids=ARRAY[inst] WHERE pid=pg_backend_pid();
 BEGIN
  INSERT INTO deanery.contact_person(last_name,first_name,middle_name,phone,email) VALUES(details->>'last_name',details->>'first_name',details->>'middle_name',details->>'phone',details->>'email') RETURNING * INTO contact;
  INSERT INTO deanery.student_contact(student_id,contact_person_id,relation_id,is_emergency) VALUES(sid,contact.contact_person_id,relation,emergency);
 EXCEPTION WHEN OTHERS THEN
  UPDATE backend.actor_context SET audit_institute_ids=saved WHERE pid=pg_backend_pid();
  RAISE;
 END;
 UPDATE backend.actor_context SET audit_institute_ids=saved WHERE pid=pg_backend_pid();
 RETURN jsonb_build_object('contact',to_jsonb(contact),'student_id',sid,'relation_id',relation,'is_emergency',emergency);
END $$;

CREATE FUNCTION backend.current_command() RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$ SELECT c.command FROM backend.actor_context c JOIN pg_stat_activity a ON a.pid=c.pid AND a.backend_start=c.backend_start WHERE c.pid=pg_backend_pid() $$;

CREATE FUNCTION backend.finish_agent_run(rid uuid, owner_id integer, new_status text, answer text) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'backend', 'deanery', 'pg_temp'
    AS $$
DECLARE run backend.agent_runs; ended timestamptz:=clock_timestamp(); saved_context backend.actor_context;
BEGIN
 SELECT * INTO run FROM backend.agent_runs WHERE id=rid FOR UPDATE;
 IF NOT FOUND OR run.user_id IS DISTINCT FROM owner_id
 THEN RAISE EXCEPTION 'Unknown or foreign run' USING ERRCODE='42501'; END IF;
 IF new_status NOT IN('done','failed','ambiguous') OR new_status IS NULL
 OR (new_status='done' AND answer IS NULL) OR length(answer)>100000
 OR (new_status<>'done' AND answer IS NOT NULL)
 THEN RAISE EXCEPTION 'Invalid run outcome' USING ERRCODE='22023'; END IF;
 IF run.status NOT IN('running','ambiguous') THEN RETURN false; END IF;
 SELECT * INTO saved_context FROM backend.actor_context WHERE pid=pg_backend_pid();
 INSERT INTO backend.actor_context(pid,backend_start,user_id,request_id,command,agent_run_id)
 SELECT pg_backend_pid(),backend_start,run.user_id,run.id::text,NULL,run.id
 FROM pg_stat_activity WHERE pid=pg_backend_pid()
 ON CONFLICT(pid) DO UPDATE SET backend_start=EXCLUDED.backend_start,user_id=EXCLUDED.user_id,
 request_id=EXCLUDED.request_id,command=NULL,agent_run_id=EXCLUDED.agent_run_id,audit_institute_ids=NULL;
 UPDATE backend.agent_runs SET status=new_status,
 completed_at=CASE WHEN new_status='ambiguous' THEN NULL ELSE ended END WHERE id=rid;
 IF run.canonical_request_id IS NOT NULL THEN
  UPDATE deanery.agent_request SET
   status=CASE new_status WHEN 'done' THEN 'success' WHEN 'failed' THEN 'error' ELSE 'pending' END,
   response_text=CASE WHEN new_status='done' THEN answer ELSE NULL END,
   execution_ms=CASE WHEN new_status='ambiguous' THEN NULL
    ELSE least(2147483647,greatest(0,floor(extract(epoch FROM ended-run.created_at)*1000)))::integer END
  WHERE agent_request_id=run.canonical_request_id AND user_id=owner_id;
 END IF;
 IF saved_context.pid IS NULL THEN
  DELETE FROM backend.actor_context WHERE pid=pg_backend_pid();
 ELSE
  UPDATE backend.actor_context SET backend_start=saved_context.backend_start,
   user_id=saved_context.user_id,request_id=saved_context.request_id,
   command=saved_context.command,agent_run_id=saved_context.agent_run_id,audit_institute_ids=saved_context.audit_institute_ids
  WHERE pid=pg_backend_pid();
 END IF;
 RETURN true;
END $$;

CREATE FUNCTION backend.group_allowed(gid integer, writing boolean DEFAULT false) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$
 SELECT coalesce(i.role_code='admin' OR (i.role_code IN('dean_staff','director') AND deanery.fn_group_institute(gid)=ANY(i.institute_ids)) OR (NOT writing AND (EXISTS(SELECT 1 FROM deanery.student s WHERE s.student_id=i.student_id AND s.group_id=gid) OR EXISTS(SELECT 1 FROM deanery.teaching_assignment t WHERE t.teacher_id=i.teacher_id AND t.group_id=gid))),false) FROM backend.identity(backend.actor()) i
$$;

CREATE FUNCTION backend.identity(uid integer) RETURNS TABLE(user_id integer, login character varying, person_id integer, is_active boolean, role_code character varying, institute_ids integer[], student_id integer, teacher_id integer)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$
 SELECT u.user_id,u.login,u.person_id,u.is_active,r.code,
 ARRAY(SELECT ds.institute_id FROM deanery.employee e JOIN deanery.dean_office_staff ds ON ds.employee_id=e.employee_id WHERE e.person_id=u.person_id AND e.dismissal_date IS NULL UNION SELECT i.institute_id FROM deanery.institute i JOIN deanery.employee e ON e.employee_id=i.director_id WHERE e.person_id=u.person_id AND e.dismissal_date IS NULL),
 (SELECT s.student_id FROM deanery.student s WHERE s.person_id=u.person_id),
 (SELECT t.employee_id FROM deanery.teacher t JOIN deanery.employee e ON e.employee_id=t.employee_id WHERE e.person_id=u.person_id AND e.dismissal_date IS NULL)
 FROM deanery.app_user u JOIN deanery.app_role r ON r.role_id=u.role_id WHERE u.user_id=uid
$$;

CREATE FUNCTION backend.is_admin() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$ SELECT coalesce((SELECT role_code='admin' FROM backend.identity(backend.actor())),false) $$;

CREATE FUNCTION backend.lock_capacity() RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'backend', 'pg_temp'
    AS $$
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Capacity-changing writes require READ COMMITTED' USING ERRCODE='40001';
 END IF;
 PERFORM pg_advisory_xact_lock(617221);
END $$;

CREATE FUNCTION backend.login_record(name text) RETURNS TABLE(user_id integer, password_hash character varying, is_active boolean)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$ SELECT u.user_id,u.password_hash,u.is_active FROM deanery.app_user u WHERE lower(login)=lower(name) $$;

CREATE FUNCTION backend.order_parent_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$
DECLARE oid integer; typ text;
BEGIN
 IF TG_TABLE_NAME='order_type' THEN
  IF NEW.code IS DISTINCT FROM OLD.code AND EXISTS(SELECT 1 FROM deanery.academic_order WHERE order_type_id=OLD.order_type_id) THEN RAISE EXCEPTION 'Used order type code is immutable'; END IF;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='order_student' THEN
  IF TG_OP='UPDATE' AND (NEW.order_id,NEW.student_id) IS NOT DISTINCT FROM (OLD.order_id,OLD.student_id) THEN RETURN NEW; END IF;
  IF EXISTS(SELECT 1 FROM deanery.scholarship WHERE order_id=OLD.order_id AND student_id=OLD.student_id UNION ALL SELECT 1 FROM deanery.practice_placement WHERE order_id=OLD.order_id AND student_id=OLD.student_id UNION ALL SELECT 1 FROM deanery.academic_work WHERE order_id=OLD.order_id AND student_id=OLD.student_id UNION ALL SELECT 1 FROM deanery.academic_leave WHERE (order_id=OLD.order_id OR return_order_id=OLD.order_id) AND student_id=OLD.student_id) THEN RAISE EXCEPTION 'Order membership is referenced by student records'; END IF;
  RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
 END IF;
 IF (NEW.order_type_id,NEW.institute_id) IS NOT DISTINCT FROM (OLD.order_type_id,OLD.institute_id) THEN RETURN NEW; END IF;
 SELECT code INTO typ FROM deanery.order_type WHERE order_type_id=NEW.order_type_id;
 IF EXISTS(SELECT 1 FROM deanery.order_student os JOIN deanery.student s USING(student_id) WHERE os.order_id=OLD.order_id AND deanery.fn_group_institute(s.group_id)<>NEW.institute_id) THEN RAISE EXCEPTION 'Order institute conflicts with membership'; END IF;
 IF (typ<>'scholarship' AND EXISTS(SELECT 1 FROM deanery.scholarship WHERE order_id=OLD.order_id)) OR (typ<>'practice' AND EXISTS(SELECT 1 FROM deanery.practice_placement WHERE order_id=OLD.order_id)) OR (typ<>'thesis_topics' AND EXISTS(SELECT 1 FROM deanery.academic_work WHERE order_id=OLD.order_id)) OR (typ<>'leave' AND EXISTS(SELECT 1 FROM deanery.academic_leave WHERE order_id=OLD.order_id)) OR (typ<>'leave_return' AND EXISTS(SELECT 1 FROM deanery.academic_leave WHERE return_order_id=OLD.order_id)) THEN RAISE EXCEPTION 'Order type conflicts with student records'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION backend.parent_integrity() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$
DECLARE r record;
BEGIN
 PERFORM backend.lock_capacity();
 IF to_jsonb(NEW)=to_jsonb(OLD) THEN RETURN NULL; END IF;
 IF EXISTS(SELECT 1 FROM deanery.curriculum_item ci JOIN deanery.curriculum c USING(curriculum_id) JOIN deanery.discipline d USING(discipline_id) WHERE ci.semester>ceil(c.duration_years*2) OR ci.module_count>0 AND d.kind<>'discipline') THEN RAISE EXCEPTION 'Parent change invalidates curriculum items'; END IF;
 IF EXISTS(SELECT 1 FROM deanery.study_group g CROSS JOIN LATERAL generate_series(1,length(deanery.fn_group_prefix(g.curriculum_id))) n WHERE substr(deanery.fn_group_prefix(g.curriculum_id),n,1)<>'?' AND substr(deanery.fn_group_prefix(g.curriculum_id),n,1)<>substr(g.name,n,1)) THEN RAISE EXCEPTION 'Parent change invalidates group code'; END IF;
 IF EXISTS(SELECT 1 FROM deanery.grade_sheet g JOIN deanery.curriculum_item ci USING(item_id) JOIN deanery.study_group sg USING(group_id) WHERE ci.curriculum_id<>sg.curriculum_id OR g.stage='module_1' AND ci.module_count<1 OR g.stage='module_2' AND ci.module_count<2) THEN RAISE EXCEPTION 'Parent change invalidates grade sheets'; END IF;
 IF EXISTS(SELECT 1 FROM deanery.teaching_assignment a JOIN deanery.curriculum_item ci USING(item_id) JOIN deanery.study_group g USING(group_id) WHERE ci.curriculum_id<>g.curriculum_id) THEN RAISE EXCEPTION 'Parent change invalidates assignments'; END IF;
 IF EXISTS(SELECT 1 FROM deanery.grade WHERE points IS NOT NULL AND deanery.fn_mark(points) IS NULL) THEN RAISE EXCEPTION 'Parent change invalidates results'; END IF;
 IF EXISTS(SELECT 1 FROM deanery.employee e JOIN deanery.position p USING(position_id) WHERE EXISTS(SELECT 1 FROM deanery.teacher t WHERE t.employee_id=e.employee_id) AND p.category<>'teaching' OR EXISTS(SELECT 1 FROM deanery.dean_office_staff s WHERE s.employee_id=e.employee_id) AND p.category='teaching') THEN RAISE EXCEPTION 'Parent change invalidates employee type'; END IF;
 IF EXISTS(SELECT 1 FROM deanery.institute i JOIN deanery.employee e ON e.employee_id=i.director_id JOIN deanery.position p USING(position_id) WHERE p.category<>'administration' OR e.dismissal_date IS NOT NULL) THEN RAISE EXCEPTION 'Parent change invalidates institute director'; END IF;
 IF EXISTS(SELECT 1 FROM deanery.scholarship s JOIN deanery.scholarship_type st USING(scholarship_type_id) JOIN deanery.student u USING(student_id) JOIN deanery.funding_type f USING(funding_type_id) WHERE st.budget_only AND f.name<>'Бюджет') THEN RAISE EXCEPTION 'Parent change invalidates scholarship funding'; END IF;
 IF EXISTS(SELECT 1 FROM deanery.practice_placement pp JOIN deanery.curriculum_item ci USING(item_id) JOIN deanery.discipline d USING(discipline_id) WHERE d.kind<>'practice') OR EXISTS(SELECT 1 FROM deanery.academic_work aw JOIN deanery.curriculum_item ci USING(item_id) JOIN deanery.discipline d USING(discipline_id) WHERE d.kind NOT IN('coursework','final_attestation')) THEN RAISE EXCEPTION 'Parent change invalidates practice or work'; END IF;
 IF TG_TABLE_NAME='classroom' THEN
  IF NEW.capacity<OLD.capacity THEN
   FOR r IN SELECT slot_id FROM deanery.schedule_slot WHERE classroom_id=NEW.classroom_id ORDER BY weekday,pair_number,slot_id LOOP PERFORM deanery.fn_check_slot(r.slot_id); END LOOP;
  END IF;
 ELSIF TG_TABLE_NAME='student_status' THEN
  IF NEW.is_active AND NOT OLD.is_active THEN
   FOR r IN SELECT DISTINCT s.slot_id,s.weekday,s.pair_number FROM deanery.schedule_slot s JOIN deanery.teaching_assignment a USING(assignment_id) JOIN deanery.student st ON st.group_id=a.group_id WHERE st.status_id=NEW.status_id ORDER BY s.weekday,s.pair_number,s.slot_id LOOP PERFORM deanery.fn_check_slot(r.slot_id); END LOOP;
  END IF;
 ELSE
  FOR r IN SELECT slot_id FROM deanery.schedule_slot ORDER BY weekday,pair_number,slot_id LOOP PERFORM deanery.fn_check_slot(r.slot_id); END LOOP;
 END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION backend.private_person_allowed(pid integer) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$
 SELECT coalesce((SELECT i.role_code='admin' OR i.person_id=pid OR (i.role_code IN('director','dean_staff') AND (
 EXISTS(SELECT 1 FROM deanery.student s WHERE s.person_id=pid AND backend.student_allowed(s.student_id,false)) OR
 EXISTS(SELECT 1 FROM deanery.employee e JOIN deanery.teacher t USING(employee_id) JOIN deanery.department d USING(department_id) WHERE e.person_id=pid AND d.institute_id=ANY(i.institute_ids)) OR
 EXISTS(SELECT 1 FROM deanery.employee e JOIN deanery.dean_office_staff ds USING(employee_id) WHERE e.person_id=pid AND ds.institute_id=ANY(i.institute_ids)) OR
 EXISTS(SELECT 1 FROM deanery.employee e JOIN deanery.institute inst ON inst.director_id=e.employee_id WHERE e.person_id=pid AND inst.institute_id=ANY(i.institute_ids))))
 FROM backend.identity(backend.actor()) i WHERE i.is_active),false)
$$;

CREATE FUNCTION backend.protected_change() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'backend', 'deanery'
    AS $$
DECLARE cmd text:=backend.current_command();
BEGIN
 IF TG_TABLE_NAME='student' THEN
  IF TG_OP='INSERT' AND cmd IS DISTINCT FROM 'enroll_student' THEN RAISE EXCEPTION 'Enrollment workflow required' USING ERRCODE='42501'; END IF;
  IF TG_OP='UPDATE' AND (NEW.group_id,NEW.status_id,NEW.enrollment_date,NEW.person_id,NEW.record_book_number) IS DISTINCT FROM (OLD.group_id,OLD.status_id,OLD.enrollment_date,OLD.person_id,OLD.record_book_number) AND coalesce(cmd,'') NOT IN('enroll_student','expel_student','transfer_student','grant_academic_leave','return_from_leave','reinstate_student') THEN RAISE EXCEPTION 'Student workflow required' USING ERRCODE='42501'; END IF;
 ELSIF TG_TABLE_NAME='grade_sheet' THEN
  IF TG_OP='INSERT' AND NEW.status<>'open' THEN RAISE EXCEPTION 'A sheet must start open'; END IF;
  IF TG_OP='UPDATE' AND OLD.status<>'open' AND to_jsonb(OLD)<>to_jsonb(NEW) THEN RAISE EXCEPTION 'Closed/cancelled sheet is immutable'; END IF;
  IF TG_OP='DELETE' AND OLD.status<>'open' THEN RAISE EXCEPTION 'Closed/cancelled sheet is immutable'; END IF;
 ELSIF TG_TABLE_NAME IN('academic_order','order_student','academic_leave') THEN
  IF cmd IS NULL THEN RAISE EXCEPTION 'Order workflow required' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;

CREATE FUNCTION backend.set_actor(uid integer, rid text) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$
BEGIN
 IF uid IS NULL THEN DELETE FROM backend.actor_context WHERE pid=pg_backend_pid(); RETURN; END IF;
 IF NOT EXISTS(SELECT 1 FROM deanery.app_user WHERE user_id=uid AND is_active) THEN RAISE EXCEPTION 'Inactive actor' USING ERRCODE='42501'; END IF;
 INSERT INTO backend.actor_context(pid,backend_start,user_id,request_id) SELECT pg_backend_pid(),backend_start,uid,left(rid,100) FROM pg_stat_activity WHERE pid=pg_backend_pid() ON CONFLICT(pid) DO UPDATE SET backend_start=EXCLUDED.backend_start,user_id=EXCLUDED.user_id,request_id=EXCLUDED.request_id,command=NULL,agent_run_id=NULL,audit_institute_ids=NULL;
END $$;

CREATE FUNCTION backend.set_audit_run(rid text) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$ DECLARE run uuid; BEGIN SELECT id INTO run FROM backend.agent_runs WHERE user_id=backend.actor() AND (id::text=rid OR upstream_run_id::text=rid); IF run IS NULL THEN RAISE EXCEPTION 'Unknown or foreign agent run' USING ERRCODE='42501'; END IF; UPDATE backend.actor_context SET agent_run_id=run WHERE pid=pg_backend_pid(); END $$;

CREATE FUNCTION backend.staff_institutes() RETURNS integer[]
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$ SELECT coalesce((SELECT institute_ids FROM backend.identity(backend.actor()) WHERE role_code IN('dean_staff','director')),ARRAY[]::integer[]) $$;

CREATE FUNCTION backend.start_agent_run(rid uuid, sid uuid, expiry timestamp with time zone, message text) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'backend', 'deanery', 'pg_temp'
    AS $$
DECLARE actor integer:=backend.actor(); request_id integer;
BEGIN
 IF actor IS NULL OR NOT EXISTS(SELECT 1 FROM public.chat_sessions WHERE id=sid AND actor_id='user:'||actor)
 THEN RAISE EXCEPTION 'Unknown or foreign session' USING ERRCODE='42501'; END IF;
 IF message IS NULL OR btrim(message)='' OR length(message)>8000 OR expiry<=clock_timestamp()
 THEN RAISE EXCEPTION 'Invalid admitted request' USING ERRCODE='22023'; END IF;
 INSERT INTO deanery.agent_request(user_id,request_text,intent_code,status)
 VALUES(actor,message,NULL,'pending') RETURNING agent_request_id INTO request_id;
 INSERT INTO backend.agent_runs(id,user_id,session_id,expires_at,canonical_request_id)
 VALUES(rid,actor,sid,expiry,request_id);
 RETURN request_id;
END $$;

CREATE FUNCTION backend.student_allowed(sid integer, writing boolean DEFAULT false) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$ SELECT backend.group_allowed(s.group_id,writing) AND (writing OR i.role_code<>'student' OR s.student_id=i.student_id) FROM deanery.student s CROSS JOIN backend.identity(backend.actor()) i WHERE s.student_id=sid $$;

CREATE FUNCTION backend.student_capacity() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'backend', 'deanery', 'pg_temp'
    AS $$
DECLARE active_new boolean; active_old boolean; slot record;
BEGIN
 -- Lock even an inactive insert: a concurrent status activation must see it.
 PERFORM backend.lock_capacity();
 SELECT is_active INTO active_new FROM deanery.student_status WHERE status_id=NEW.status_id;
 IF NOT active_new THEN RETURN NULL; END IF;
 IF TG_OP='UPDATE' THEN
  SELECT is_active INTO active_old FROM deanery.student_status WHERE status_id=OLD.status_id;
  IF active_old AND NEW.group_id=OLD.group_id THEN RETURN NULL; END IF;
 END IF;
 FOR slot IN SELECT DISTINCT s.weekday*10+s.pair_number AS lock_key
  FROM deanery.schedule_slot s JOIN deanery.teaching_assignment a USING(assignment_id)
  WHERE a.group_id=NEW.group_id ORDER BY lock_key
 LOOP PERFORM pg_advisory_xact_lock(4242,slot.lock_key); END LOOP;
 FOR slot IN SELECT s.slot_id FROM deanery.schedule_slot s
  JOIN deanery.teaching_assignment a USING(assignment_id) WHERE a.group_id=NEW.group_id ORDER BY s.slot_id
 LOOP PERFORM deanery.fn_check_slot(slot.slot_id); END LOOP;
 RETURN NULL;
END $$;

CREATE FUNCTION backend.touch_login(uid integer) RETURNS void
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$ UPDATE deanery.app_user SET last_login_at=now() WHERE user_id=uid $$;

CREATE FUNCTION backend.visible_ids(rel text, writing boolean) RETURNS integer[]
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
    AS $$
DECLARE i record; gids integer[]; sids integer[]; result integer[];
BEGIN
 SELECT * INTO i FROM backend.identity(backend.actor());
 IF i.user_id IS NULL THEN RETURN ARRAY[]::integer[]; END IF;
 SELECT coalesce(array_agg(g.group_id),ARRAY[]::integer[]) INTO gids FROM deanery.study_group g WHERE i.role_code='admin' OR (i.role_code IN('director','dean_staff') AND deanery.fn_group_institute(g.group_id)=ANY(i.institute_ids)) OR (NOT writing AND (EXISTS(SELECT 1 FROM deanery.student s WHERE s.student_id=i.student_id AND s.group_id=g.group_id) OR EXISTS(SELECT 1 FROM deanery.teaching_assignment a WHERE a.group_id=g.group_id AND a.teacher_id=i.teacher_id)));
 IF rel='study_group' THEN RETURN gids; END IF;
 SELECT coalesce(array_agg(s.student_id),ARRAY[]::integer[]) INTO sids FROM deanery.student s WHERE s.group_id=ANY(gids) AND (i.role_code<>'student' OR s.student_id=i.student_id);
 IF rel='student' THEN RETURN sids; END IF;
 IF rel='person' THEN SELECT array_agg(p.person_id) INTO result FROM deanery.person p WHERE (NOT writing AND (p.person_id=i.person_id OR EXISTS(SELECT 1 FROM deanery.employee e WHERE e.person_id=p.person_id))) OR EXISTS(SELECT 1 FROM deanery.student s WHERE s.person_id=p.person_id AND s.student_id=ANY(sids));
 ELSIF rel='grade_sheet' THEN SELECT array_agg(gs.sheet_id) INTO result FROM deanery.grade_sheet gs WHERE gs.group_id=ANY(gids) OR gs.examiner_id=i.teacher_id OR (NOT writing AND EXISTS(SELECT 1 FROM deanery.grade gr WHERE gr.sheet_id=gs.sheet_id AND gr.student_id=ANY(sids)));
 ELSIF rel='examiner_sheet' THEN SELECT array_agg(gs.sheet_id) INTO result FROM deanery.grade_sheet gs WHERE gs.examiner_id=i.teacher_id;
 ELSIF rel='teaching_assignment' THEN SELECT array_agg(a.assignment_id) INTO result FROM deanery.teaching_assignment a WHERE a.group_id=ANY(gids);
 ELSIF rel='schedule_slot' THEN SELECT array_agg(ss.slot_id) INTO result FROM deanery.schedule_slot ss JOIN deanery.teaching_assignment a USING(assignment_id) WHERE a.group_id=ANY(gids);
 END IF;
 RETURN coalesce(result,ARRAY[]::integer[]);
END $$;

CREATE TABLE backend.actor_context (
    pid integer NOT NULL,
    backend_start timestamp with time zone NOT NULL,
    user_id integer,
    request_id text NOT NULL,
    command text,
    agent_run_id uuid,
    audit_institute_ids integer[]
);

CREATE TABLE backend.agent_runs (
    id uuid NOT NULL,
    user_id integer NOT NULL,
    session_id uuid NOT NULL,
    upstream_run_id uuid,
    status text DEFAULT 'running'::text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    canonical_request_id integer,
    CONSTRAINT agent_runs_status_check CHECK ((status = ANY (ARRAY['running'::text, 'done'::text, 'failed'::text, 'ambiguous'::text])))
);

CREATE TABLE backend.command_results (
    user_id integer NOT NULL,
    key text NOT NULL,
    name text NOT NULL,
    payload_hash text NOT NULL,
    result jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE backend.delegation_nonces (
    nonce uuid NOT NULL,
    expires_at timestamp with time zone NOT NULL
);

CREATE TABLE backend.domain_events (
 id uuid PRIMARY KEY,
 user_id integer NOT NULL REFERENCES deanery.app_user(user_id),
 name text NOT NULL,
 order_id integer NOT NULL,
 student_id integer NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 cancelled_at timestamptz,
 UNIQUE(order_id,student_id)
);

CREATE TABLE backend.events (
    id bigint NOT NULL,
    user_id integer,
    request_id text,
    action text NOT NULL,
    resource text NOT NULL,
    record_key jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE backend.events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME backend.events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE backend.file_chunks (
    id uuid NOT NULL,
    version_id uuid NOT NULL,
    ordinal integer NOT NULL,
    page integer,
    content text NOT NULL,
    CONSTRAINT file_chunks_page_check CHECK (((page IS NULL) OR (page > 0)))
);

CREATE TABLE backend.file_versions (
    id uuid NOT NULL,
    file_id uuid NOT NULL,
    version integer NOT NULL,
    object_key text NOT NULL,
    filename text NOT NULL,
    mime text NOT NULL,
    byte_size bigint NOT NULL,
    sha256 text NOT NULL,
    state text NOT NULL,
    quality jsonb DEFAULT '{}'::jsonb NOT NULL,
    error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT file_versions_byte_size_check CHECK (((byte_size >= 1) AND (byte_size <= 10000000))),
    CONSTRAINT file_versions_sha256_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT file_versions_state_check CHECK ((state = ANY (ARRAY['quarantined'::text, 'scanning'::text, 'parsing'::text, 'indexing'::text, 'ready'::text, 'rejected'::text, 'failed'::text]))),
    CONSTRAINT file_versions_version_check CHECK ((version > 0))
);

CREATE TABLE backend.files (
    id uuid NOT NULL,
    owner_id integer NOT NULL,
    institute_id integer,
    association jsonb,
    title text NOT NULL,
    source text DEFAULT ''::text NOT NULL,
    purpose text NOT NULL,
    active_version_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT files_association_check CHECK (((association IS NULL) OR ((jsonb_typeof(association) = 'object'::text) AND (association ? 'resource'::text) AND (association ? 'key'::text)))),
    CONSTRAINT files_purpose_check CHECK ((purpose = ANY (ARRAY['attachment'::text, 'regulation'::text, 'export'::text])))
);

CREATE TABLE backend.jobs (
    id uuid NOT NULL,
    owner_id integer NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    idempotency_key text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    max_attempts integer NOT NULL,
    progress integer DEFAULT 0 NOT NULL,
    result jsonb,
    error_code text,
    queue_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT jobs_kind_check CHECK ((kind = ANY (ARRAY['process_file'::text, 'export_report'::text, 'reconcile'::text, 'apply_workflow'::text]))),
    CONSTRAINT jobs_max_attempts_check CHECK (((max_attempts >= 1) AND (max_attempts <= 10))),
    CONSTRAINT jobs_progress_check CHECK (((progress >= 0) AND (progress <= 100))),
    CONSTRAINT jobs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text])))
);

CREATE TABLE backend.login_attempts (
    key text NOT NULL,
    started_at timestamp with time zone NOT NULL,
    attempts integer NOT NULL
);

CREATE TABLE backend.object_intents (
    object_key text NOT NULL,
    version_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE backend.refresh_tokens (
    token_hash text NOT NULL,
    session_id uuid NOT NULL,
    used_at timestamp with time zone
);

CREATE TABLE backend.sessions (
    id uuid NOT NULL,
    user_id integer NOT NULL,
    csrf_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone
);

ALTER TABLE ONLY backend.actor_context
    ADD CONSTRAINT actor_context_pkey PRIMARY KEY (pid);

ALTER TABLE ONLY backend.agent_runs
    ADD CONSTRAINT agent_runs_canonical_request_id_key UNIQUE (canonical_request_id);

ALTER TABLE ONLY backend.agent_runs
    ADD CONSTRAINT agent_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY backend.command_results
    ADD CONSTRAINT command_results_pkey PRIMARY KEY (user_id, key);

ALTER TABLE ONLY backend.delegation_nonces
    ADD CONSTRAINT delegation_nonces_pkey PRIMARY KEY (nonce);

ALTER TABLE ONLY backend.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY backend.file_chunks
    ADD CONSTRAINT file_chunks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY backend.file_chunks
    ADD CONSTRAINT file_chunks_version_id_ordinal_key UNIQUE (version_id, ordinal);

ALTER TABLE ONLY backend.file_versions
    ADD CONSTRAINT file_versions_file_id_id_key UNIQUE (file_id, id);

ALTER TABLE ONLY backend.file_versions
    ADD CONSTRAINT file_versions_file_id_version_key UNIQUE (file_id, version);

ALTER TABLE ONLY backend.file_versions
    ADD CONSTRAINT file_versions_object_key_key UNIQUE (object_key);

ALTER TABLE ONLY backend.file_versions
    ADD CONSTRAINT file_versions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY backend.files
    ADD CONSTRAINT files_pkey PRIMARY KEY (id);

ALTER TABLE ONLY backend.jobs
    ADD CONSTRAINT jobs_owner_id_kind_idempotency_key_key UNIQUE (owner_id, kind, idempotency_key);

ALTER TABLE ONLY backend.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY backend.login_attempts
    ADD CONSTRAINT login_attempts_pkey PRIMARY KEY (key);

ALTER TABLE ONLY backend.object_intents
    ADD CONSTRAINT object_intents_pkey PRIMARY KEY (object_key);

ALTER TABLE ONLY backend.refresh_tokens
    ADD CONSTRAINT refresh_tokens_pkey PRIMARY KEY (token_hash);

ALTER TABLE ONLY backend.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX agent_one_active_session ON backend.agent_runs USING btree (session_id) WHERE (status = ANY (ARRAY['running'::text, 'ambiguous'::text]));

CREATE INDEX sessions_user ON backend.sessions USING btree (user_id);

CREATE TRIGGER agent_run_identity_immutable BEFORE UPDATE ON backend.agent_runs FOR EACH ROW EXECUTE FUNCTION backend.agent_run_identity_immutable();

CREATE TRIGGER events_immutable BEFORE DELETE OR UPDATE ON backend.events FOR EACH ROW EXECUTE FUNCTION deanery.trg_audit_readonly();

ALTER TABLE ONLY backend.actor_context
    ADD CONSTRAINT actor_context_agent_run_id_fkey FOREIGN KEY (agent_run_id) REFERENCES backend.agent_runs(id);

ALTER TABLE ONLY backend.actor_context
    ADD CONSTRAINT actor_context_user_id_fkey FOREIGN KEY (user_id) REFERENCES deanery.app_user(user_id);

ALTER TABLE ONLY backend.agent_runs
    ADD CONSTRAINT agent_runs_canonical_request_id_fkey FOREIGN KEY (canonical_request_id) REFERENCES deanery.agent_request(agent_request_id);

ALTER TABLE ONLY backend.agent_runs
    ADD CONSTRAINT agent_runs_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.chat_sessions(id);

ALTER TABLE ONLY backend.agent_runs
    ADD CONSTRAINT agent_runs_user_id_fkey FOREIGN KEY (user_id) REFERENCES deanery.app_user(user_id);

ALTER TABLE ONLY backend.command_results
    ADD CONSTRAINT command_results_user_id_fkey FOREIGN KEY (user_id) REFERENCES deanery.app_user(user_id);

ALTER TABLE ONLY backend.events
    ADD CONSTRAINT events_user_id_fkey FOREIGN KEY (user_id) REFERENCES deanery.app_user(user_id);

ALTER TABLE ONLY backend.file_chunks
    ADD CONSTRAINT file_chunks_version_id_fkey FOREIGN KEY (version_id) REFERENCES backend.file_versions(id);

ALTER TABLE ONLY backend.file_versions
    ADD CONSTRAINT file_versions_file_id_fkey FOREIGN KEY (file_id) REFERENCES backend.files(id);

ALTER TABLE ONLY backend.files
    ADD CONSTRAINT files_active_version_fk FOREIGN KEY (id, active_version_id) REFERENCES backend.file_versions(file_id, id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY backend.files
    ADD CONSTRAINT files_institute_id_fkey FOREIGN KEY (institute_id) REFERENCES deanery.institute(institute_id);

ALTER TABLE ONLY backend.files
    ADD CONSTRAINT files_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES deanery.app_user(user_id);

ALTER TABLE ONLY backend.jobs
    ADD CONSTRAINT jobs_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES deanery.app_user(user_id);

ALTER TABLE ONLY backend.refresh_tokens
    ADD CONSTRAINT refresh_tokens_session_id_fkey FOREIGN KEY (session_id) REFERENCES backend.sessions(id);

ALTER TABLE ONLY backend.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES deanery.app_user(user_id);

GRANT USAGE ON SCHEMA backend TO deanery_runtime;
GRANT USAGE ON SCHEMA backend TO deanery_jobs;
GRANT USAGE ON SCHEMA backend TO deanery_reader;

REVOKE ALL ON FUNCTION backend.actor() FROM PUBLIC;
GRANT ALL ON FUNCTION backend.actor() TO deanery_runtime;
GRANT ALL ON FUNCTION backend.actor() TO deanery_jobs;
GRANT ALL ON FUNCTION backend.actor() TO deanery_reader;

REVOKE ALL ON FUNCTION backend.agent_run_identity_immutable() FROM PUBLIC;

REVOKE ALL ON FUNCTION backend.allowed(rel text, r jsonb, writing boolean) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.allowed(rel text, r jsonb, writing boolean) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.allowed(rel text, r jsonb, writing boolean) TO deanery_jobs;
GRANT ALL ON FUNCTION backend.allowed(rel text, r jsonb, writing boolean) TO deanery_reader;

REVOKE ALL ON FUNCTION backend.audit_actor() FROM PUBLIC;

REVOKE ALL ON FUNCTION backend.audit_agent_request() FROM PUBLIC;

REVOKE ALL ON FUNCTION backend.audit_allowed(institutes integer[], global_scope boolean) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.audit_allowed(institutes integer[], global_scope boolean) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.audit_allowed(institutes integer[], global_scope boolean) TO deanery_jobs;
GRANT ALL ON FUNCTION backend.audit_allowed(institutes integer[], global_scope boolean) TO deanery_reader;

REVOKE ALL ON FUNCTION backend.audit_global(rel text) FROM PUBLIC;

REVOKE ALL ON FUNCTION backend.audit_institutes(rel text, r jsonb, inserting boolean) FROM PUBLIC;

REVOKE ALL ON FUNCTION backend.audit_request() FROM PUBLIC;

REVOKE ALL ON FUNCTION backend.audit_run() FROM PUBLIC;

REVOKE ALL ON FUNCTION backend.change_password(uid integer, encoded text) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.change_password(uid integer, encoded text) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.change_password(uid integer, encoded text) TO deanery_jobs;

REVOKE ALL ON FUNCTION backend.command(cmd text) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.command(cmd text) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.command(cmd text) TO deanery_jobs;

REVOKE ALL ON FUNCTION backend.contact_parent_scope() FROM PUBLIC;

REVOKE ALL ON FUNCTION backend.create_student_contact(sid integer, details jsonb, relation integer, emergency boolean) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.create_student_contact(sid integer, details jsonb, relation integer, emergency boolean) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.create_student_contact(sid integer, details jsonb, relation integer, emergency boolean) TO deanery_jobs;

REVOKE ALL ON FUNCTION backend.current_command() FROM PUBLIC;
GRANT ALL ON FUNCTION backend.current_command() TO deanery_runtime;
GRANT ALL ON FUNCTION backend.current_command() TO deanery_jobs;
GRANT ALL ON FUNCTION backend.current_command() TO deanery_reader;

REVOKE ALL ON FUNCTION backend.finish_agent_run(rid uuid, owner_id integer, new_status text, answer text) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.finish_agent_run(rid uuid, owner_id integer, new_status text, answer text) TO deanery_runtime;

REVOKE ALL ON FUNCTION backend.group_allowed(gid integer, writing boolean) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.group_allowed(gid integer, writing boolean) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.group_allowed(gid integer, writing boolean) TO deanery_jobs;
GRANT ALL ON FUNCTION backend.group_allowed(gid integer, writing boolean) TO deanery_reader;

REVOKE ALL ON FUNCTION backend.identity(uid integer) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.identity(uid integer) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.identity(uid integer) TO deanery_jobs;

REVOKE ALL ON FUNCTION backend.is_admin() FROM PUBLIC;
GRANT ALL ON FUNCTION backend.is_admin() TO deanery_runtime;
GRANT ALL ON FUNCTION backend.is_admin() TO deanery_jobs;
GRANT ALL ON FUNCTION backend.is_admin() TO deanery_reader;

REVOKE ALL ON FUNCTION backend.lock_capacity() FROM PUBLIC;

REVOKE ALL ON FUNCTION backend.login_record(name text) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.login_record(name text) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.login_record(name text) TO deanery_jobs;

REVOKE ALL ON FUNCTION backend.order_parent_guard() FROM PUBLIC;

REVOKE ALL ON FUNCTION backend.parent_integrity() FROM PUBLIC;

REVOKE ALL ON FUNCTION backend.private_person_allowed(pid integer) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.private_person_allowed(pid integer) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.private_person_allowed(pid integer) TO deanery_jobs;

REVOKE ALL ON FUNCTION backend.protected_change() FROM PUBLIC;

REVOKE ALL ON FUNCTION backend.set_actor(uid integer, rid text) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.set_actor(uid integer, rid text) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.set_actor(uid integer, rid text) TO deanery_jobs;

REVOKE ALL ON FUNCTION backend.set_audit_run(rid text) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.set_audit_run(rid text) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.set_audit_run(rid text) TO deanery_jobs;

REVOKE ALL ON FUNCTION backend.staff_institutes() FROM PUBLIC;
GRANT ALL ON FUNCTION backend.staff_institutes() TO deanery_runtime;
GRANT ALL ON FUNCTION backend.staff_institutes() TO deanery_jobs;
GRANT ALL ON FUNCTION backend.staff_institutes() TO deanery_reader;

REVOKE ALL ON FUNCTION backend.start_agent_run(rid uuid, sid uuid, expiry timestamp with time zone, message text) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.start_agent_run(rid uuid, sid uuid, expiry timestamp with time zone, message text) TO deanery_runtime;

REVOKE ALL ON FUNCTION backend.student_allowed(sid integer, writing boolean) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.student_allowed(sid integer, writing boolean) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.student_allowed(sid integer, writing boolean) TO deanery_jobs;
GRANT ALL ON FUNCTION backend.student_allowed(sid integer, writing boolean) TO deanery_reader;

REVOKE ALL ON FUNCTION backend.student_capacity() FROM PUBLIC;

REVOKE ALL ON FUNCTION backend.touch_login(uid integer) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.touch_login(uid integer) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.touch_login(uid integer) TO deanery_jobs;

REVOKE ALL ON FUNCTION backend.visible_ids(rel text, writing boolean) FROM PUBLIC;
GRANT ALL ON FUNCTION backend.visible_ids(rel text, writing boolean) TO deanery_runtime;
GRANT ALL ON FUNCTION backend.visible_ids(rel text, writing boolean) TO deanery_jobs;
GRANT ALL ON FUNCTION backend.visible_ids(rel text, writing boolean) TO deanery_reader;

GRANT SELECT,UPDATE ON TABLE backend.agent_runs TO deanery_runtime;
GRANT SELECT,UPDATE ON TABLE backend.agent_runs TO deanery_jobs;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.command_results TO deanery_runtime;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.command_results TO deanery_jobs;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.delegation_nonces TO deanery_runtime;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.delegation_nonces TO deanery_jobs;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.domain_events TO deanery_runtime;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.domain_events TO deanery_jobs;

GRANT SELECT,INSERT ON TABLE backend.events TO deanery_runtime;
GRANT SELECT,INSERT ON TABLE backend.events TO deanery_jobs;

GRANT SELECT,USAGE ON SEQUENCE backend.events_id_seq TO deanery_runtime;
GRANT SELECT,USAGE ON SEQUENCE backend.events_id_seq TO deanery_jobs;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.file_chunks TO deanery_runtime;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.file_chunks TO deanery_jobs;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.file_versions TO deanery_runtime;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.file_versions TO deanery_jobs;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.files TO deanery_runtime;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.files TO deanery_jobs;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.jobs TO deanery_runtime;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.jobs TO deanery_jobs;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.login_attempts TO deanery_runtime;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.login_attempts TO deanery_jobs;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.object_intents TO deanery_runtime;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.object_intents TO deanery_jobs;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.refresh_tokens TO deanery_runtime;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.refresh_tokens TO deanery_jobs;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.sessions TO deanery_runtime;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE backend.sessions TO deanery_jobs;
SET row_security=on;
SET check_function_bodies=on;

SET check_function_bodies = true;
