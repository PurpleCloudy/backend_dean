-- The native canonical procedures own domain state. These small entrypoints add
-- the application's verified principal, institute scope and audit correlation.
CREATE OR REPLACE FUNCTION deanery.fn_current_app_user() RETURNS integer
LANGUAGE plpgsql STABLE SET search_path=pg_catalog,backend,pg_temp AS $$
DECLARE uid integer:=backend.actor();
BEGIN
 IF uid IS NULL THEN RAISE EXCEPTION 'Active authenticated user required' USING ERRCODE='42501'; END IF;
 RETURN uid;
END $$;

CREATE FUNCTION backend.student_workflow(command_name text, data jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,deanery,backend,pg_temp AS $$
DECLARE actor_record record; sid integer:=(data->>'student_id')::integer; gid integer;
 oid integer; eff date; saved_scope integer[]; result jsonb; old_student deanery.student;
BEGIN
 SELECT * INTO actor_record FROM backend.identity(backend.actor());
 IF actor_record.user_id IS NULL OR NOT actor_record.is_active OR actor_record.role_code NOT IN('admin','director','dean_staff')
 THEN RAISE EXCEPTION 'Deanery staff required' USING ERRCODE='42501'; END IF;
 PERFORM backend.lock_capacity();
 IF sid IS NOT NULL THEN
  SELECT * INTO old_student FROM deanery.student WHERE student_id=sid FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Student not found' USING ERRCODE='P0002'; END IF;
  IF NOT backend.student_allowed(sid,true)
  THEN RAISE EXCEPTION 'Student outside your institute' USING ERRCODE='42501'; END IF;
 END IF;
 IF data->>'group_name' IS NOT NULL THEN
  SELECT group_id INTO gid FROM deanery.study_group WHERE name=data->>'group_name' AND NOT is_archived FOR SHARE;
  IF gid IS NULL OR NOT coalesce(backend.group_allowed(gid,true),false)
  THEN RAISE EXCEPTION 'Active group outside your institute' USING ERRCODE='42501'; END IF;
 END IF;
 PERFORM backend.command(command_name);
 eff:=coalesce((data->>'effective_date')::date,CURRENT_DATE);
 IF command_name='enroll_student' THEN
  SELECT order_id INTO oid FROM deanery.academic_order WHERE order_number=data->>'order_number' FOR UPDATE;
  IF oid IS NOT NULL AND NOT EXISTS(SELECT 1 FROM deanery.academic_order o JOIN deanery.order_type t USING(order_type_id)
   WHERE o.order_id=oid AND t.code='enroll' AND o.institute_id=deanery.fn_group_institute(gid)
    AND o.signed_by_id=(data->>'signed_by')::integer AND o.order_date=coalesce((data->>'order_date')::date,CURRENT_DATE)
    AND o.effective_date=eff) THEN RAISE EXCEPTION 'Existing enrollment order differs'; END IF;
  SELECT audit_institute_ids INTO saved_scope FROM backend.actor_context WHERE pid=pg_backend_pid();
  UPDATE backend.actor_context SET audit_institute_ids=ARRAY[deanery.fn_group_institute(gid)] WHERE pid=pg_backend_pid();
  BEGIN
   sid:=deanery.fn_enroll_student(data->>'last_name',data->>'first_name',data->>'middle_name',
    (data->>'gender')::char,(data->>'birth_date')::date,data->>'email',data->>'phone',data->>'group_name',
    data->>'funding',data->>'record_book',data->>'order_number',coalesce((data->>'order_date')::date,CURRENT_DATE),
    (data->>'signed_by')::integer,eff);
  EXCEPTION WHEN OTHERS THEN
   UPDATE backend.actor_context SET audit_institute_ids=saved_scope WHERE pid=pg_backend_pid(); RAISE;
  END;
  UPDATE backend.actor_context SET audit_institute_ids=saved_scope WHERE pid=pg_backend_pid();
 ELSE
  IF data->>'order_date' IS NOT NULL AND (data->>'order_date')::date<>CURRENT_DATE
  THEN RAISE EXCEPTION 'Movement orders are signed today; effective date is separate'; END IF;
  CASE command_name
   WHEN 'expel_student' THEN CALL deanery.sp_expel_student(sid,data->>'order_number',data->>'reason',(data->>'signed_by')::integer,eff);
   WHEN 'transfer_student' THEN CALL deanery.sp_transfer_student(sid,data->>'group_name',data->>'order_number',(data->>'signed_by')::integer,eff);
   WHEN 'grant_academic_leave' THEN CALL deanery.sp_grant_academic_leave(sid,data->>'reason',(data->>'start')::date,(data->>'end')::date,data->>'order_number',(data->>'signed_by')::integer);
   WHEN 'return_from_leave' THEN CALL deanery.sp_return_from_leave(sid,data->>'order_number',(data->>'signed_by')::integer,eff,data->>'group_name');
   WHEN 'reinstate_student' THEN CALL deanery.sp_reinstate_student(sid,data->>'group_name',data->>'order_number',(data->>'signed_by')::integer,eff);
   WHEN 'create_order' THEN
    IF data->>'type_code' NOT IN('scholarship','practice','thesis_topics') THEN RAISE EXCEPTION 'Unsupported order type'; END IF;
    oid:=deanery.fn_student_order(sid,data->>'type_code',data->>'order_number',data->>'title',
     (data->>'signed_by')::integer,data->>'reason',NULL,CURRENT_DATE,eff);
   ELSE RAISE EXCEPTION 'Unknown student command';
  END CASE;
 END IF;
 SELECT o.order_id INTO oid FROM deanery.academic_order o JOIN deanery.order_student os USING(order_id)
 WHERE o.order_number=data->>'order_number' AND os.student_id=sid;
 SELECT jsonb_build_object('student_id',sid,'order_id',oid,'effective_date',deanery.fn_order_due_date(oid,sid),
   'pending',os.applied_at IS NULL) INTO result FROM deanery.order_student os WHERE os.order_id=oid AND os.student_id=sid;
 RETURN result;
END $$;

CREATE FUNCTION backend.apply_scheduled_order(event_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,deanery,backend,pg_temp AS $$
DECLARE ev backend.domain_events; member deanery.order_student; typ text; uid integer:=backend.actor();
BEGIN
 PERFORM backend.lock_capacity();
 SELECT * INTO ev FROM backend.domain_events WHERE id=event_id AND user_id=uid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Scheduled order not found' USING ERRCODE='P0002'; END IF;
 IF ev.cancelled_at IS NOT NULL THEN RETURN jsonb_build_object('event_id',event_id,'status','cancelled'); END IF;
 SELECT * INTO member FROM deanery.order_student WHERE order_id=ev.order_id AND student_id=ev.student_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Scheduled order membership not found'; END IF;
 IF NOT EXISTS(SELECT 1 FROM backend.identity(uid) WHERE is_active AND role_code IN('admin','director','dean_staff'))
  OR NOT backend.student_allowed(ev.student_id,true)
  OR (member.new_group_id IS NOT NULL AND NOT backend.group_allowed(member.new_group_id,true))
 THEN RAISE EXCEPTION 'Current authority no longer permits this order' USING ERRCODE='42501'; END IF;
 PERFORM backend.command(ev.name);
 PERFORM deanery.fn_apply_order(ev.order_id,ev.student_id);
 RETURN jsonb_build_object('student_id',ev.student_id,'order_id',ev.order_id,'effective_date',deanery.fn_order_due_date(ev.order_id,ev.student_id));
END $$;

CREATE FUNCTION backend.cancel_scheduled_order(event_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,deanery,backend,pg_temp AS $$
DECLARE ev backend.domain_events; number text;
BEGIN
 PERFORM backend.lock_capacity();
 SELECT * INTO ev FROM backend.domain_events WHERE id=event_id AND user_id=backend.actor() FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Scheduled order not found' USING ERRCODE='P0002'; END IF;
 IF ev.cancelled_at IS NOT NULL THEN RETURN jsonb_build_object('event_id',event_id,'status','cancelled'); END IF;
 IF NOT backend.student_allowed(ev.student_id,true) THEN RAISE EXCEPTION 'Student outside institute' USING ERRCODE='42501'; END IF;
 SELECT order_number INTO number FROM deanery.academic_order WHERE order_id=ev.order_id;
 PERFORM backend.command('cancel_scheduled');
 CALL deanery.sp_cancel_scheduled_order(number,ev.student_id);
 UPDATE backend.domain_events SET cancelled_at=now() WHERE id=event_id;
 RETURN jsonb_build_object('event_id',event_id,'status','cancelled');
END $$;

CREATE FUNCTION backend.request_grade_correction(sheet_number text, record_book text, points integer, absent boolean, reason text) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,deanery,backend,pg_temp AS $$
DECLARE g record; cid integer; identity record;
BEGIN
 SELECT * INTO identity FROM backend.identity(backend.actor());
 SELECT gs.*,gr.grade_id,gr.student_id INTO g FROM deanery.grade gr JOIN deanery.grade_sheet gs USING(sheet_id)
 JOIN deanery.student st ON st.student_id=gr.student_id
 WHERE gs.sheet_number=request_grade_correction.sheet_number AND st.record_book_number=record_book FOR UPDATE OF gr,gs;
 IF NOT FOUND OR identity.user_id IS NULL OR NOT identity.is_active OR identity.role_code='student'
  OR NOT (identity.role_code='admin' OR (identity.role_code IN('dean_staff','director') AND backend.group_allowed(g.group_id,true))
    OR (identity.role_code='teacher' AND g.examiner_id=identity.teacher_id))
 THEN RAISE EXCEPTION 'Grade correction is outside your authority' USING ERRCODE='42501'; END IF;
 cid:=deanery.fn_request_grade_correction(sheet_number,record_book,points,absent,reason);
 INSERT INTO backend.events(user_id,request_id,action,resource,record_key)
 VALUES(backend.actor(),backend.audit_request(),'grade_correction_requested','grade_correction',
  jsonb_build_object('correction_id',cid,'origin_run_id',backend.audit_run()));
 RETURN cid;
END $$;

CREATE FUNCTION backend.set_correction_audit(cid integer) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,deanery,backend,pg_temp AS $$
DECLARE c deanery.grade_correction; origin uuid; recorded_origin uuid; uid integer:=backend.actor(); employee_id integer; institute_id integer;
BEGIN
 SELECT gc.* INTO c FROM deanery.grade_correction gc WHERE correction_id=cid FOR UPDATE;
 IF NOT FOUND OR c.status<>'pending' OR uid IS NULL OR uid=c.requested_by_id THEN RAISE EXCEPTION 'Correction cannot be decided' USING ERRCODE='42501'; END IF;
 SELECT e.employee_id INTO employee_id FROM deanery.app_user u JOIN deanery.employee e USING(person_id) WHERE u.user_id=uid AND u.is_active;
 SELECT deanery.fn_group_institute(gs.group_id) INTO institute_id FROM deanery.grade g JOIN deanery.grade_sheet gs USING(sheet_id) WHERE g.grade_id=c.grade_id;
 IF employee_id IS NULL OR NOT deanery.fn_manages_institute(employee_id,institute_id) THEN RAISE EXCEPTION 'Institute director or deputy required' USING ERRCODE='42501'; END IF;
 SELECT (e.record_key->>'origin_run_id')::uuid INTO recorded_origin FROM backend.events e
 WHERE e.action='grade_correction_requested' AND e.resource='grade_correction'
 AND (e.record_key->>'correction_id')::integer=cid AND e.user_id=c.requested_by_id ORDER BY e.id LIMIT 1;
 SELECT r.id INTO origin FROM backend.events e JOIN backend.agent_runs r ON r.id=(e.record_key->>'origin_run_id')::uuid
 JOIN public.chat_sessions s ON s.id=r.session_id AND s.actor_id='user:'||r.user_id
 JOIN deanery.agent_request ar ON ar.agent_request_id=r.canonical_request_id AND ar.user_id=r.user_id
 WHERE e.action='grade_correction_requested' AND e.resource='grade_correction'
 AND (e.record_key->>'correction_id')::integer=cid AND e.user_id=c.requested_by_id AND r.user_id=c.requested_by_id
 ORDER BY e.id LIMIT 1;
 IF recorded_origin IS NOT NULL AND origin IS NULL THEN RAISE EXCEPTION 'Correction audit chain is invalid' USING ERRCODE='42501'; END IF;
 UPDATE backend.actor_context SET agent_run_id=origin WHERE pid=pg_backend_pid();
END $$;

CREATE FUNCTION backend.decide_grade_correction(cid integer, approve boolean, comment text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,deanery,backend,pg_temp AS $$
DECLARE result text;
BEGIN
 PERFORM backend.set_correction_audit(cid);
 PERFORM 1 FROM deanery.grade g JOIN deanery.grade_correction c USING(grade_id) WHERE c.correction_id=cid FOR UPDATE OF g;
 CALL deanery.sp_decide_grade_correction(cid,approve,comment);
 SELECT status INTO result FROM deanery.grade_correction WHERE correction_id=cid;
 RETURN result;
END $$;

CREATE FUNCTION backend.set_proposal_audit(pid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,deanery,backend,pg_temp AS $$
DECLARE origin uuid;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM backend.identity(backend.actor()) WHERE is_active AND role_code IN('admin','director','dean_staff'))
 THEN RAISE EXCEPTION 'Active reviewer required' USING ERRCODE='42501'; END IF;
 SELECT r.id INTO origin FROM public.sql_change_proposals p
 JOIN backend.agent_runs r ON (r.id::text=p.agent_run_id OR r.upstream_run_id::text=p.agent_run_id)
  AND r.user_id=p.owner_id AND r.session_id=p.session_id
 JOIN public.chat_sessions s ON s.id=p.session_id AND s.actor_id='user:'||p.owner_id
 JOIN deanery.agent_request ar ON ar.agent_request_id=r.canonical_request_id AND ar.user_id=p.owner_id
 WHERE p.id=pid AND p.status='pending' AND p.expires_at>now() AND p.actor_id='user:'||p.owner_id FOR UPDATE OF p;
 IF origin IS NULL THEN RAISE EXCEPTION 'Proposal audit chain is invalid' USING ERRCODE='42501'; END IF;
 UPDATE backend.actor_context SET agent_run_id=origin WHERE backend.actor_context.pid=pg_backend_pid();
END $$;

CREATE FUNCTION backend.correction_user_label(uid integer) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,deanery,backend,pg_temp AS $$
 SELECT u.login::text FROM deanery.app_user u WHERE u.user_id=uid AND EXISTS(
  SELECT 1 FROM deanery.grade_correction c WHERE uid IN(c.requested_by_id,c.decided_by_id)
   AND backend.allowed('grade_correction',to_jsonb(c),false))
$$;

CREATE FUNCTION backend.protect_correction_origin() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,backend,pg_temp AS $$
BEGIN
 IF NEW.action='grade_correction_requested' AND current_user<>(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='backend.events'::regclass)
 THEN RAISE EXCEPTION 'Trusted correction origin required' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER correction_origin BEFORE INSERT ON backend.events FOR EACH ROW EXECUTE FUNCTION backend.protect_correction_origin();

REVOKE ALL ON FUNCTION backend.student_workflow(text,jsonb),backend.apply_scheduled_order(uuid),backend.cancel_scheduled_order(uuid),
 backend.request_grade_correction(text,text,integer,boolean,text),backend.set_correction_audit(integer),backend.decide_grade_correction(integer,boolean,text),
 backend.set_proposal_audit(uuid),backend.correction_user_label(integer),backend.protect_correction_origin() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION backend.student_workflow(text,jsonb),backend.apply_scheduled_order(uuid),backend.cancel_scheduled_order(uuid) TO deanery_runtime,deanery_jobs;
GRANT EXECUTE ON FUNCTION backend.request_grade_correction(text,text,integer,boolean,text),backend.decide_grade_correction(integer,boolean,text),backend.set_proposal_audit(uuid) TO deanery_runtime;
GRANT EXECUTE ON FUNCTION backend.correction_user_label(integer) TO deanery_runtime,deanery_jobs,deanery_reader;
