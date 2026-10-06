-- Application constraints and scoped audit; canonical business schema stays in sources/deanery_db.
ALTER TABLE deanery.audit_log ADD COLUMN request_id text, ADD COLUMN agent_run_id uuid, ADD COLUMN scope_institute_ids integer[] NOT NULL DEFAULT ARRAY[]::integer[], ADD COLUMN scope_global boolean NOT NULL DEFAULT false;
CREATE OR REPLACE FUNCTION deanery.trg_audit()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
AS $function$
DECLARE oldrow jsonb:=CASE WHEN TG_OP<>'INSERT' THEN to_jsonb(OLD) END; newrow jsonb:=CASE WHEN TG_OP<>'DELETE' THEN to_jsonb(NEW) END; rec jsonb; rid text; aid bigint;
BEGIN
 IF TG_OP='UPDATE' AND oldrow=newrow THEN RETURN NULL; END IF;
 rec:=coalesce(newrow,oldrow);
 SELECT string_agg(a.attname||'='||coalesce(rec->>a.attname,'NULL'),', ' ORDER BY k.ord) INTO rid FROM pg_index i CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,ord) JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum WHERE i.indrelid=TG_RELID AND i.indisprimary;
 INSERT INTO deanery.audit_log(table_name,record_key,operation,db_user,app_user_id,request_id,agent_run_id,agent_request_id,scope_institute_ids,scope_global) VALUES(TG_TABLE_NAME,rid,left(TG_OP,1),session_user,backend.audit_actor(),backend.audit_request(),backend.audit_run(),backend.audit_agent_request(),ARRAY(SELECT DISTINCT i FROM unnest(backend.audit_institutes(TG_TABLE_NAME,oldrow,false)||backend.audit_institutes(TG_TABLE_NAME,newrow,TG_OP='INSERT')) i ORDER BY i),backend.audit_global(TG_TABLE_NAME)) RETURNING audit_id INTO aid;
 INSERT INTO deanery.audit_log_detail(audit_id,column_name,old_value,new_value) SELECT aid,coalesce(o.key,n.key), CASE WHEN o.value IS NULL THEN NULL WHEN coalesce(o.key,n.key)=ANY(ARRAY['password_hash','token_hash','csrf_hash','passport_series','passport_number','snils','inn','address','birth_date','phone','email','last_name','first_name','middle_name','gender','personnel_number','request_text','response_text','reason','decision_comment','purpose','topic','duties','title']) THEN '[redacted]' ELSE o.value END,CASE WHEN n.value IS NULL THEN NULL WHEN coalesce(o.key,n.key)=ANY(ARRAY['password_hash','token_hash','csrf_hash','passport_series','passport_number','snils','inn','address','birth_date','phone','email','last_name','first_name','middle_name','gender','personnel_number','request_text','response_text','reason','decision_comment','purpose','topic','duties','title']) THEN '[redacted]' ELSE n.value END FROM jsonb_each_text(coalesce(oldrow,'{}')) o FULL JOIN jsonb_each_text(coalesce(newrow,'{}')) n ON o.key=n.key WHERE o.value IS DISTINCT FROM n.value;
 INSERT INTO backend.events(user_id,request_id,action,resource,record_key) SELECT backend.audit_actor(),request_id,TG_OP,TG_TABLE_NAME,jsonb_build_object('key',rid) FROM backend.actor_context WHERE pid=pg_backend_pid() AND backend_start=(SELECT backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid());
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NULL;
END $function$;

CREATE OR REPLACE FUNCTION deanery.fn_check_slot(p_slot_id integer)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
AS $function$
DECLARE
    c RECORD;
    v_people INT;
    v_capacity INT;
BEGIN
 PERFORM backend.lock_capacity();
 PERFORM pg_advisory_xact_lock(4242,weekday*10+pair_number) FROM deanery.schedule_slot WHERE slot_id=p_slot_id;
    -- период занятия должен лежать внутри учебного семестра
    IF EXISTS (SELECT 1 FROM schedule_slot s JOIN academic_term t ON t.term_id = s.term_id
               WHERE s.slot_id = p_slot_id
                 AND NOT daterange(t.start_date, t.end_date, '[]') @> fn_slot_period(s)) THEN
        RAISE EXCEPTION 'Период занятия выходит за даты занятий семестра';
    END IF;

    FOR c IN
        WITH me AS (
            SELECT s.*, fn_slot_period(s) AS period, a.teacher_id, a.group_id, a.subgroup, ci.discipline_id, lt.name AS lesson
            FROM schedule_slot s
            JOIN teaching_assignment a ON a.assignment_id = s.assignment_id
            JOIN curriculum_item ci    ON ci.item_id = a.item_id
            JOIN lesson_type lt        ON lt.lesson_type_id = a.lesson_type_id
            WHERE s.slot_id = p_slot_id),
        other AS (
            SELECT s.*, fn_slot_period(s) AS period, a.teacher_id, a.group_id, a.subgroup, ci.discipline_id, lt.name AS lesson
            FROM schedule_slot s
            JOIN teaching_assignment a ON a.assignment_id = s.assignment_id
            JOIN curriculum_item ci    ON ci.item_id = a.item_id
            JOIN lesson_type lt        ON lt.lesson_type_id = a.lesson_type_id)
        SELECT o.slot_id, me.weekday, me.pair_number,
               (me.teacher_id = o.teacher_id AND me.classroom_id = o.classroom_id
                AND me.discipline_id = o.discipline_id
                AND me.lesson = 'Лекция' AND o.lesson = 'Лекция') AS is_stream,
               me.classroom_id = o.classroom_id AS same_room,
               me.teacher_id = o.teacher_id AS same_teacher,
               (me.group_id = o.group_id AND NOT (me.subgroup IS NOT NULL AND o.subgroup IS NOT NULL
                                                  AND me.subgroup <> o.subgroup)) AS same_group,
               me.classroom_id, me.teacher_id, me.group_id
        FROM me JOIN other o
          ON o.slot_id <> me.slot_id AND o.weekday = me.weekday AND o.pair_number = me.pair_number
         AND (o.week_parity = 'every' OR me.week_parity = 'every' OR o.week_parity = me.week_parity)
         AND o.period && me.period
    LOOP
        IF c.same_group THEN
            RAISE EXCEPTION 'Конфликт расписания: у группы % уже есть занятие (%, % пара)',
                (SELECT name FROM study_group WHERE group_id = c.group_id),
                (ARRAY['пн','вт','ср','чт','пт','сб'])[c.weekday], c.pair_number;
        ELSIF c.same_teacher AND NOT c.is_stream THEN
            RAISE EXCEPTION 'Конфликт расписания: преподаватель % уже занят (%, % пара)',
                (SELECT fn_full_name(p) FROM employee e JOIN person p ON p.person_id = e.person_id
                 WHERE e.employee_id = c.teacher_id),
                (ARRAY['пн','вт','ср','чт','пт','сб'])[c.weekday], c.pair_number;
        ELSIF c.same_room AND NOT c.is_stream THEN
            RAISE EXCEPTION 'Конфликт расписания: аудитория % занята (%, % пара)',
                (SELECT building || '-' || room_number FROM classroom WHERE classroom_id = c.classroom_id),
                (ARRAY['пн','вт','ср','чт','пт','сб'])[c.weekday], c.pair_number;
        END IF;
    END LOOP;

    -- вместимость: все группы, которые сидят в этой аудитории в это время (поток)
    SELECT SUM(CASE WHEN a.subgroup IS NULL THEN n.cnt ELSE CEIL(n.cnt / 2.0) END), MAX(cr.capacity)
    INTO v_people, v_capacity
    FROM schedule_slot me
    JOIN schedule_slot s ON s.classroom_id = me.classroom_id AND s.weekday = me.weekday
                        AND s.pair_number = me.pair_number
                        AND (s.week_parity = 'every' OR me.week_parity = 'every' OR s.week_parity = me.week_parity)
                        AND fn_slot_period(s) && fn_slot_period(me)
    JOIN teaching_assignment a ON a.assignment_id = s.assignment_id
    JOIN classroom cr ON cr.classroom_id = me.classroom_id
    JOIN LATERAL (SELECT COUNT(*) AS cnt FROM student st JOIN student_status ss ON ss.status_id = st.status_id
                  WHERE st.group_id = a.group_id AND ss.is_active) n ON TRUE
    WHERE me.slot_id = p_slot_id;

    IF v_people > v_capacity THEN
        RAISE EXCEPTION 'В аудитории % мест, а на занятии будет % студентов', v_capacity, v_people;
    END IF;
END $function$;

CREATE OR REPLACE FUNCTION deanery.trg_schedule_conflicts()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'deanery', 'backend', 'public', 'pg_temp'
AS $function$
DECLARE s RECORD;
BEGIN
 PERFORM backend.lock_capacity();
    IF TG_TABLE_NAME = 'schedule_slot' THEN
        -- сериализуем вставки в одну и ту же пару, чтобы параллельные транзакции не проскочили
        PERFORM pg_advisory_xact_lock(4242, NEW.weekday * 10 + NEW.pair_number);
        PERFORM fn_check_slot(NEW.slot_id);
    ELSE
        FOR s IN SELECT slot_id, weekday, pair_number FROM schedule_slot WHERE assignment_id = NEW.assignment_id LOOP
            PERFORM pg_advisory_xact_lock(4242, s.weekday * 10 + s.pair_number);
            PERFORM fn_check_slot(s.slot_id);
        END LOOP;
    END IF;
    RETURN NULL;
END $function$;

CREATE OR REPLACE FUNCTION deanery.trg_scholarship_check()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE v_active BOOLEAN; v_funding TEXT;
BEGIN
    SELECT st.is_active, ft.name INTO v_active, v_funding
    FROM student s
    JOIN student_status st ON st.status_id = s.status_id
    JOIN funding_type   ft ON ft.funding_type_id = s.funding_type_id
    WHERE s.student_id = NEW.student_id;

    IF (TG_OP = 'INSERT' OR NEW.student_id<>OLD.student_id) AND NOT v_active THEN
        RAISE EXCEPTION 'Студент % сейчас не обучается — стипендию назначить нельзя', NEW.student_id;
    END IF;
    IF (SELECT budget_only FROM scholarship_type WHERE scholarship_type_id = NEW.scholarship_type_id)
       AND v_funding <> 'Бюджет' THEN
        RAISE EXCEPTION 'Эта стипендия назначается только студентам на бюджете, а студент % учится по договору',
            NEW.student_id;
    END IF;
    RETURN NEW;
END $function$;

ALTER TABLE deanery.academic_degree ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.academic_leave ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.academic_order ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.academic_term ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.academic_title ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.academic_work ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.agent_intent ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.agent_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.app_role ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.app_user ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.attendance ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.audit_log_detail ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.classroom ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.contact_person ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.contact_relation ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.control_type ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.curriculum ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.curriculum_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.dean_office_staff ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.department ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.discipline ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.document_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.document_type ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.education_level ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.employee ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.funding_type ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.grade ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.grade_sheet ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.holiday ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.institute ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.leave_reason ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.lesson_type ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.order_student ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.order_type ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.organization ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.organization_contact ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.pair_time ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.person ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery."position" ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.practice_placement ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.request_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.schedule_slot ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.scholarship ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.scholarship_type ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.score_band ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.specialty ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.student ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.student_contact ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.student_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.study_form ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.study_group ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.study_program ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.teacher ENABLE ROW LEVEL SECURITY;
ALTER TABLE deanery.teaching_assignment ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_delete ON deanery.academic_degree FOR DELETE TO public USING (backend.allowed('academic_degree'::text, to_jsonb(academic_degree.*), true));
CREATE POLICY scoped_insert ON deanery.academic_degree FOR INSERT TO public WITH CHECK (backend.allowed('academic_degree'::text, to_jsonb(academic_degree.*), true));
CREATE POLICY scoped_read ON deanery.academic_degree FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.academic_degree FOR UPDATE TO public USING (backend.allowed('academic_degree'::text, to_jsonb(academic_degree.*), true)) WITH CHECK (backend.allowed('academic_degree'::text, to_jsonb(academic_degree.*), true));
CREATE POLICY scoped_delete ON deanery.academic_leave FOR DELETE TO public USING (backend.allowed('academic_leave'::text, to_jsonb(academic_leave.*), true));
CREATE POLICY scoped_insert ON deanery.academic_leave FOR INSERT TO public WITH CHECK (backend.allowed('academic_leave'::text, to_jsonb(academic_leave.*), true));
CREATE POLICY scoped_read ON deanery.academic_leave FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('academic_leave'::text, to_jsonb(academic_leave.*), false)));
CREATE POLICY scoped_update ON deanery.academic_leave FOR UPDATE TO public USING (backend.allowed('academic_leave'::text, to_jsonb(academic_leave.*), true)) WITH CHECK (backend.allowed('academic_leave'::text, to_jsonb(academic_leave.*), true));
CREATE POLICY scoped_delete ON deanery.academic_order FOR DELETE TO public USING (backend.allowed('academic_order'::text, to_jsonb(academic_order.*), true));
CREATE POLICY scoped_insert ON deanery.academic_order FOR INSERT TO public WITH CHECK (backend.allowed('academic_order'::text, to_jsonb(academic_order.*), true));
CREATE POLICY scoped_read ON deanery.academic_order FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('academic_order'::text, to_jsonb(academic_order.*), false)));
CREATE POLICY scoped_update ON deanery.academic_order FOR UPDATE TO public USING (backend.allowed('academic_order'::text, to_jsonb(academic_order.*), true)) WITH CHECK (backend.allowed('academic_order'::text, to_jsonb(academic_order.*), true));
CREATE POLICY scoped_delete ON deanery.academic_term FOR DELETE TO public USING (( SELECT backend.is_admin() AS is_admin));
CREATE POLICY scoped_insert ON deanery.academic_term FOR INSERT TO public WITH CHECK (backend.allowed('academic_term'::text, to_jsonb(academic_term.*), true));
CREATE POLICY scoped_read ON deanery.academic_term FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.academic_term FOR UPDATE TO public USING (backend.allowed('academic_term'::text, to_jsonb(academic_term.*), true)) WITH CHECK (backend.allowed('academic_term'::text, to_jsonb(academic_term.*), true));
CREATE POLICY scoped_delete ON deanery.academic_title FOR DELETE TO public USING (backend.allowed('academic_title'::text, to_jsonb(academic_title.*), true));
CREATE POLICY scoped_insert ON deanery.academic_title FOR INSERT TO public WITH CHECK (backend.allowed('academic_title'::text, to_jsonb(academic_title.*), true));
CREATE POLICY scoped_read ON deanery.academic_title FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.academic_title FOR UPDATE TO public USING (backend.allowed('academic_title'::text, to_jsonb(academic_title.*), true)) WITH CHECK (backend.allowed('academic_title'::text, to_jsonb(academic_title.*), true));
CREATE POLICY scoped_delete ON deanery.academic_work FOR DELETE TO public USING (backend.allowed('academic_work'::text, to_jsonb(academic_work.*), true));
CREATE POLICY scoped_insert ON deanery.academic_work FOR INSERT TO public WITH CHECK (backend.allowed('academic_work'::text, to_jsonb(academic_work.*), true));
CREATE POLICY scoped_read ON deanery.academic_work FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('academic_work'::text, to_jsonb(academic_work.*), false)));
CREATE POLICY scoped_update ON deanery.academic_work FOR UPDATE TO public USING (backend.allowed('academic_work'::text, to_jsonb(academic_work.*), true)) WITH CHECK (backend.allowed('academic_work'::text, to_jsonb(academic_work.*), true));
CREATE POLICY scoped_delete ON deanery.agent_intent FOR DELETE TO public USING (backend.allowed('agent_intent'::text, to_jsonb(agent_intent.*), true));
CREATE POLICY scoped_insert ON deanery.agent_intent FOR INSERT TO public WITH CHECK (backend.allowed('agent_intent'::text, to_jsonb(agent_intent.*), true));
CREATE POLICY scoped_read ON deanery.agent_intent FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.agent_intent FOR UPDATE TO public USING (backend.allowed('agent_intent'::text, to_jsonb(agent_intent.*), true)) WITH CHECK (backend.allowed('agent_intent'::text, to_jsonb(agent_intent.*), true));
CREATE POLICY scoped_delete ON deanery.agent_request FOR DELETE TO public USING (backend.allowed('agent_request'::text, to_jsonb(agent_request.*), true));
CREATE POLICY scoped_insert ON deanery.agent_request FOR INSERT TO public WITH CHECK (backend.allowed('agent_request'::text, to_jsonb(agent_request.*), true));
CREATE POLICY scoped_read ON deanery.agent_request FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('agent_request'::text, to_jsonb(agent_request.*), false)));
CREATE POLICY scoped_update ON deanery.agent_request FOR UPDATE TO public USING (backend.allowed('agent_request'::text, to_jsonb(agent_request.*), true)) WITH CHECK (backend.allowed('agent_request'::text, to_jsonb(agent_request.*), true));
CREATE POLICY scoped_delete ON deanery.app_role FOR DELETE TO public USING (backend.allowed('app_role'::text, to_jsonb(app_role.*), true));
CREATE POLICY scoped_insert ON deanery.app_role FOR INSERT TO public WITH CHECK (backend.allowed('app_role'::text, to_jsonb(app_role.*), true));
CREATE POLICY scoped_read ON deanery.app_role FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('app_role'::text, to_jsonb(app_role.*), false)));
CREATE POLICY scoped_update ON deanery.app_role FOR UPDATE TO public USING (backend.allowed('app_role'::text, to_jsonb(app_role.*), true)) WITH CHECK (backend.allowed('app_role'::text, to_jsonb(app_role.*), true));
CREATE POLICY scoped_delete ON deanery.app_user FOR DELETE TO public USING (backend.allowed('app_user'::text, to_jsonb(app_user.*), true));
CREATE POLICY scoped_insert ON deanery.app_user FOR INSERT TO public WITH CHECK (backend.allowed('app_user'::text, to_jsonb(app_user.*), true));
CREATE POLICY scoped_read ON deanery.app_user FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('app_user'::text, to_jsonb(app_user.*), false)));
CREATE POLICY scoped_update ON deanery.app_user FOR UPDATE TO public USING (backend.allowed('app_user'::text, to_jsonb(app_user.*), true)) WITH CHECK (backend.allowed('app_user'::text, to_jsonb(app_user.*), true));
CREATE POLICY scoped_delete ON deanery.attendance FOR DELETE TO public USING (backend.allowed('attendance'::text, to_jsonb(attendance.*), true));
CREATE POLICY scoped_insert ON deanery.attendance FOR INSERT TO public WITH CHECK (backend.allowed('attendance'::text, to_jsonb(attendance.*), true));
CREATE POLICY scoped_read ON deanery.attendance FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR ((student_id = ANY (( SELECT backend.visible_ids('student'::text, false) AS visible_ids)::integer[])) OR (slot_id = ANY (( SELECT backend.visible_ids('schedule_slot'::text, true) AS visible_ids)::integer[])))));
CREATE POLICY scoped_update ON deanery.attendance FOR UPDATE TO public USING (backend.allowed('attendance'::text, to_jsonb(attendance.*), true)) WITH CHECK (backend.allowed('attendance'::text, to_jsonb(attendance.*), true));
CREATE POLICY scoped_delete ON deanery.audit_log FOR DELETE TO public USING (backend.allowed('audit_log'::text, to_jsonb(audit_log.*), true));
CREATE POLICY scoped_insert ON deanery.audit_log FOR INSERT TO public WITH CHECK (backend.allowed('audit_log'::text, to_jsonb(audit_log.*), true));
CREATE POLICY scoped_read ON deanery.audit_log FOR SELECT TO public USING (backend.audit_allowed(scope_institute_ids, scope_global));
CREATE POLICY scoped_update ON deanery.audit_log FOR UPDATE TO public USING (backend.allowed('audit_log'::text, to_jsonb(audit_log.*), true)) WITH CHECK (backend.allowed('audit_log'::text, to_jsonb(audit_log.*), true));
CREATE POLICY scoped_delete ON deanery.audit_log_detail FOR DELETE TO public USING (backend.allowed('audit_log_detail'::text, to_jsonb(audit_log_detail.*), true));
CREATE POLICY scoped_insert ON deanery.audit_log_detail FOR INSERT TO public WITH CHECK (backend.allowed('audit_log_detail'::text, to_jsonb(audit_log_detail.*), true));
CREATE POLICY scoped_read ON deanery.audit_log_detail FOR SELECT TO public USING ((EXISTS ( SELECT 1
   FROM deanery.audit_log a
  WHERE (a.audit_id = audit_log_detail.audit_id))));
CREATE POLICY scoped_update ON deanery.audit_log_detail FOR UPDATE TO public USING (backend.allowed('audit_log_detail'::text, to_jsonb(audit_log_detail.*), true)) WITH CHECK (backend.allowed('audit_log_detail'::text, to_jsonb(audit_log_detail.*), true));
CREATE POLICY scoped_delete ON deanery.classroom FOR DELETE TO public USING (( SELECT backend.is_admin() AS is_admin));
CREATE POLICY scoped_insert ON deanery.classroom FOR INSERT TO public WITH CHECK (backend.allowed('classroom'::text, to_jsonb(classroom.*), true));
CREATE POLICY scoped_read ON deanery.classroom FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.classroom FOR UPDATE TO public USING (backend.allowed('classroom'::text, to_jsonb(classroom.*), true)) WITH CHECK (backend.allowed('classroom'::text, to_jsonb(classroom.*), true));
CREATE POLICY scoped_delete ON deanery.contact_person FOR DELETE TO public USING (backend.allowed('contact_person'::text, to_jsonb(contact_person.*), true));
CREATE POLICY scoped_insert ON deanery.contact_person FOR INSERT TO public WITH CHECK (backend.allowed('contact_person'::text, to_jsonb(contact_person.*), true));
CREATE POLICY scoped_read ON deanery.contact_person FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('contact_person'::text, to_jsonb(contact_person.*), false)));
CREATE POLICY scoped_update ON deanery.contact_person FOR UPDATE TO public USING (backend.allowed('contact_person'::text, to_jsonb(contact_person.*), true)) WITH CHECK (backend.allowed('contact_person'::text, to_jsonb(contact_person.*), true));
CREATE POLICY scoped_delete ON deanery.contact_relation FOR DELETE TO public USING (( SELECT backend.is_admin() AS is_admin));
CREATE POLICY scoped_insert ON deanery.contact_relation FOR INSERT TO public WITH CHECK (backend.allowed('contact_relation'::text, to_jsonb(contact_relation.*), true));
CREATE POLICY scoped_read ON deanery.contact_relation FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.contact_relation FOR UPDATE TO public USING (backend.allowed('contact_relation'::text, to_jsonb(contact_relation.*), true)) WITH CHECK (backend.allowed('contact_relation'::text, to_jsonb(contact_relation.*), true));
CREATE POLICY scoped_delete ON deanery.control_type FOR DELETE TO public USING (backend.allowed('control_type'::text, to_jsonb(control_type.*), true));
CREATE POLICY scoped_insert ON deanery.control_type FOR INSERT TO public WITH CHECK (backend.allowed('control_type'::text, to_jsonb(control_type.*), true));
CREATE POLICY scoped_read ON deanery.control_type FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.control_type FOR UPDATE TO public USING (backend.allowed('control_type'::text, to_jsonb(control_type.*), true)) WITH CHECK (backend.allowed('control_type'::text, to_jsonb(control_type.*), true));
CREATE POLICY scoped_delete ON deanery.curriculum FOR DELETE TO public USING (backend.allowed('curriculum'::text, to_jsonb(curriculum.*), true));
CREATE POLICY scoped_insert ON deanery.curriculum FOR INSERT TO public WITH CHECK (backend.allowed('curriculum'::text, to_jsonb(curriculum.*), true));
CREATE POLICY scoped_read ON deanery.curriculum FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.curriculum FOR UPDATE TO public USING (backend.allowed('curriculum'::text, to_jsonb(curriculum.*), true)) WITH CHECK (backend.allowed('curriculum'::text, to_jsonb(curriculum.*), true));
CREATE POLICY scoped_delete ON deanery.curriculum_item FOR DELETE TO public USING (backend.allowed('curriculum_item'::text, to_jsonb(curriculum_item.*), true));
CREATE POLICY scoped_insert ON deanery.curriculum_item FOR INSERT TO public WITH CHECK (backend.allowed('curriculum_item'::text, to_jsonb(curriculum_item.*), true));
CREATE POLICY scoped_read ON deanery.curriculum_item FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.curriculum_item FOR UPDATE TO public USING (backend.allowed('curriculum_item'::text, to_jsonb(curriculum_item.*), true)) WITH CHECK (backend.allowed('curriculum_item'::text, to_jsonb(curriculum_item.*), true));
CREATE POLICY scoped_delete ON deanery.dean_office_staff FOR DELETE TO public USING (backend.allowed('dean_office_staff'::text, to_jsonb(dean_office_staff.*), true));
CREATE POLICY scoped_insert ON deanery.dean_office_staff FOR INSERT TO public WITH CHECK (backend.allowed('dean_office_staff'::text, to_jsonb(dean_office_staff.*), true));
CREATE POLICY scoped_read ON deanery.dean_office_staff FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.dean_office_staff FOR UPDATE TO public USING (backend.allowed('dean_office_staff'::text, to_jsonb(dean_office_staff.*), true)) WITH CHECK (backend.allowed('dean_office_staff'::text, to_jsonb(dean_office_staff.*), true));
CREATE POLICY scoped_delete ON deanery.department FOR DELETE TO public USING (backend.allowed('department'::text, to_jsonb(department.*), true));
CREATE POLICY scoped_insert ON deanery.department FOR INSERT TO public WITH CHECK (backend.allowed('department'::text, to_jsonb(department.*), true));
CREATE POLICY scoped_read ON deanery.department FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.department FOR UPDATE TO public USING (backend.allowed('department'::text, to_jsonb(department.*), true)) WITH CHECK (backend.allowed('department'::text, to_jsonb(department.*), true));
CREATE POLICY scoped_delete ON deanery.discipline FOR DELETE TO public USING (backend.allowed('discipline'::text, to_jsonb(discipline.*), true));
CREATE POLICY scoped_insert ON deanery.discipline FOR INSERT TO public WITH CHECK (backend.allowed('discipline'::text, to_jsonb(discipline.*), true));
CREATE POLICY scoped_read ON deanery.discipline FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.discipline FOR UPDATE TO public USING (backend.allowed('discipline'::text, to_jsonb(discipline.*), true)) WITH CHECK (backend.allowed('discipline'::text, to_jsonb(discipline.*), true));
CREATE POLICY scoped_delete ON deanery.document_request FOR DELETE TO public USING (backend.allowed('document_request'::text, to_jsonb(document_request.*), true));
CREATE POLICY scoped_insert ON deanery.document_request FOR INSERT TO public WITH CHECK (backend.allowed('document_request'::text, to_jsonb(document_request.*), true));
CREATE POLICY scoped_read ON deanery.document_request FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('document_request'::text, to_jsonb(document_request.*), false)));
CREATE POLICY scoped_update ON deanery.document_request FOR UPDATE TO public USING (backend.allowed('document_request'::text, to_jsonb(document_request.*), true)) WITH CHECK (backend.allowed('document_request'::text, to_jsonb(document_request.*), true));
CREATE POLICY scoped_delete ON deanery.document_type FOR DELETE TO public USING (( SELECT backend.is_admin() AS is_admin));
CREATE POLICY scoped_insert ON deanery.document_type FOR INSERT TO public WITH CHECK (backend.allowed('document_type'::text, to_jsonb(document_type.*), true));
CREATE POLICY scoped_read ON deanery.document_type FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.document_type FOR UPDATE TO public USING (backend.allowed('document_type'::text, to_jsonb(document_type.*), true)) WITH CHECK (backend.allowed('document_type'::text, to_jsonb(document_type.*), true));
CREATE POLICY scoped_delete ON deanery.education_level FOR DELETE TO public USING (backend.allowed('education_level'::text, to_jsonb(education_level.*), true));
CREATE POLICY scoped_insert ON deanery.education_level FOR INSERT TO public WITH CHECK (backend.allowed('education_level'::text, to_jsonb(education_level.*), true));
CREATE POLICY scoped_read ON deanery.education_level FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.education_level FOR UPDATE TO public USING (backend.allowed('education_level'::text, to_jsonb(education_level.*), true)) WITH CHECK (backend.allowed('education_level'::text, to_jsonb(education_level.*), true));
CREATE POLICY scoped_delete ON deanery.employee FOR DELETE TO public USING (backend.allowed('employee'::text, to_jsonb(employee.*), true));
CREATE POLICY scoped_insert ON deanery.employee FOR INSERT TO public WITH CHECK (backend.allowed('employee'::text, to_jsonb(employee.*), true));
CREATE POLICY scoped_read ON deanery.employee FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.employee FOR UPDATE TO public USING (backend.allowed('employee'::text, to_jsonb(employee.*), true)) WITH CHECK (backend.allowed('employee'::text, to_jsonb(employee.*), true));
CREATE POLICY scoped_delete ON deanery.funding_type FOR DELETE TO public USING (backend.allowed('funding_type'::text, to_jsonb(funding_type.*), true));
CREATE POLICY scoped_insert ON deanery.funding_type FOR INSERT TO public WITH CHECK (backend.allowed('funding_type'::text, to_jsonb(funding_type.*), true));
CREATE POLICY scoped_read ON deanery.funding_type FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.funding_type FOR UPDATE TO public USING (backend.allowed('funding_type'::text, to_jsonb(funding_type.*), true)) WITH CHECK (backend.allowed('funding_type'::text, to_jsonb(funding_type.*), true));
CREATE POLICY scoped_delete ON deanery.grade FOR DELETE TO public USING (backend.allowed('grade'::text, to_jsonb(grade.*), true));
CREATE POLICY scoped_insert ON deanery.grade FOR INSERT TO public WITH CHECK (backend.allowed('grade'::text, to_jsonb(grade.*), true));
CREATE POLICY scoped_read ON deanery.grade FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR ((student_id = ANY (( SELECT backend.visible_ids('student'::text, false) AS visible_ids)::integer[])) OR (sheet_id = ANY (( SELECT backend.visible_ids('examiner_sheet'::text, false) AS visible_ids)::integer[])))));
CREATE POLICY scoped_update ON deanery.grade FOR UPDATE TO public USING (backend.allowed('grade'::text, to_jsonb(grade.*), true)) WITH CHECK (backend.allowed('grade'::text, to_jsonb(grade.*), true));
CREATE POLICY scoped_delete ON deanery.grade_sheet FOR DELETE TO public USING (backend.allowed('grade_sheet'::text, to_jsonb(grade_sheet.*), true));
CREATE POLICY scoped_insert ON deanery.grade_sheet FOR INSERT TO public WITH CHECK (backend.allowed('grade_sheet'::text, to_jsonb(grade_sheet.*), true));
CREATE POLICY scoped_read ON deanery.grade_sheet FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (sheet_id = ANY (( SELECT backend.visible_ids('grade_sheet'::text, false) AS visible_ids)::integer[]))));
CREATE POLICY scoped_update ON deanery.grade_sheet FOR UPDATE TO public USING (backend.allowed('grade_sheet'::text, to_jsonb(grade_sheet.*), true)) WITH CHECK (backend.allowed('grade_sheet'::text, to_jsonb(grade_sheet.*), true));
CREATE POLICY scoped_delete ON deanery.holiday FOR DELETE TO public USING (( SELECT backend.is_admin() AS is_admin));
CREATE POLICY scoped_insert ON deanery.holiday FOR INSERT TO public WITH CHECK (backend.allowed('holiday'::text, to_jsonb(holiday.*), true));
CREATE POLICY scoped_read ON deanery.holiday FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.holiday FOR UPDATE TO public USING (backend.allowed('holiday'::text, to_jsonb(holiday.*), true)) WITH CHECK (backend.allowed('holiday'::text, to_jsonb(holiday.*), true));
CREATE POLICY scoped_delete ON deanery.institute FOR DELETE TO public USING (backend.allowed('institute'::text, to_jsonb(institute.*), true));
CREATE POLICY scoped_insert ON deanery.institute FOR INSERT TO public WITH CHECK (backend.allowed('institute'::text, to_jsonb(institute.*), true));
CREATE POLICY scoped_read ON deanery.institute FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.institute FOR UPDATE TO public USING (backend.allowed('institute'::text, to_jsonb(institute.*), true)) WITH CHECK (backend.allowed('institute'::text, to_jsonb(institute.*), true));
CREATE POLICY scoped_delete ON deanery.leave_reason FOR DELETE TO public USING (( SELECT backend.is_admin() AS is_admin));
CREATE POLICY scoped_insert ON deanery.leave_reason FOR INSERT TO public WITH CHECK (backend.allowed('leave_reason'::text, to_jsonb(leave_reason.*), true));
CREATE POLICY scoped_read ON deanery.leave_reason FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.leave_reason FOR UPDATE TO public USING (backend.allowed('leave_reason'::text, to_jsonb(leave_reason.*), true)) WITH CHECK (backend.allowed('leave_reason'::text, to_jsonb(leave_reason.*), true));
CREATE POLICY scoped_delete ON deanery.lesson_type FOR DELETE TO public USING (backend.allowed('lesson_type'::text, to_jsonb(lesson_type.*), true));
CREATE POLICY scoped_insert ON deanery.lesson_type FOR INSERT TO public WITH CHECK (backend.allowed('lesson_type'::text, to_jsonb(lesson_type.*), true));
CREATE POLICY scoped_read ON deanery.lesson_type FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.lesson_type FOR UPDATE TO public USING (backend.allowed('lesson_type'::text, to_jsonb(lesson_type.*), true)) WITH CHECK (backend.allowed('lesson_type'::text, to_jsonb(lesson_type.*), true));
CREATE POLICY scoped_delete ON deanery.order_student FOR DELETE TO public USING (backend.allowed('order_student'::text, to_jsonb(order_student.*), true));
CREATE POLICY scoped_insert ON deanery.order_student FOR INSERT TO public WITH CHECK (backend.allowed('order_student'::text, to_jsonb(order_student.*), true));
CREATE POLICY scoped_read ON deanery.order_student FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('order_student'::text, to_jsonb(order_student.*), false)));
CREATE POLICY scoped_update ON deanery.order_student FOR UPDATE TO public USING (backend.allowed('order_student'::text, to_jsonb(order_student.*), true)) WITH CHECK (backend.allowed('order_student'::text, to_jsonb(order_student.*), true));
CREATE POLICY scoped_delete ON deanery.order_type FOR DELETE TO public USING (( SELECT backend.is_admin() AS is_admin));
CREATE POLICY scoped_insert ON deanery.order_type FOR INSERT TO public WITH CHECK (backend.allowed('order_type'::text, to_jsonb(order_type.*), true));
CREATE POLICY scoped_read ON deanery.order_type FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.order_type FOR UPDATE TO public USING (backend.allowed('order_type'::text, to_jsonb(order_type.*), true)) WITH CHECK (backend.allowed('order_type'::text, to_jsonb(order_type.*), true));
CREATE POLICY scoped_delete ON deanery.organization FOR DELETE TO public USING (backend.allowed('organization'::text, to_jsonb(organization.*), true));
CREATE POLICY scoped_insert ON deanery.organization FOR INSERT TO public WITH CHECK (backend.allowed('organization'::text, to_jsonb(organization.*), true));
CREATE POLICY scoped_read ON deanery.organization FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('organization'::text, to_jsonb(organization.*), false)));
CREATE POLICY scoped_update ON deanery.organization FOR UPDATE TO public USING (backend.allowed('organization'::text, to_jsonb(organization.*), true)) WITH CHECK (backend.allowed('organization'::text, to_jsonb(organization.*), true));
CREATE POLICY scoped_delete ON deanery.organization_contact FOR DELETE TO public USING (backend.allowed('organization_contact'::text, to_jsonb(organization_contact.*), true));
CREATE POLICY scoped_insert ON deanery.organization_contact FOR INSERT TO public WITH CHECK (backend.allowed('organization_contact'::text, to_jsonb(organization_contact.*), true));
CREATE POLICY scoped_read ON deanery.organization_contact FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('organization_contact'::text, to_jsonb(organization_contact.*), false)));
CREATE POLICY scoped_update ON deanery.organization_contact FOR UPDATE TO public USING (backend.allowed('organization_contact'::text, to_jsonb(organization_contact.*), true)) WITH CHECK (backend.allowed('organization_contact'::text, to_jsonb(organization_contact.*), true));
CREATE POLICY scoped_delete ON deanery.pair_time FOR DELETE TO public USING (backend.allowed('pair_time'::text, to_jsonb(pair_time.*), true));
CREATE POLICY scoped_insert ON deanery.pair_time FOR INSERT TO public WITH CHECK (backend.allowed('pair_time'::text, to_jsonb(pair_time.*), true));
CREATE POLICY scoped_read ON deanery.pair_time FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.pair_time FOR UPDATE TO public USING (backend.allowed('pair_time'::text, to_jsonb(pair_time.*), true)) WITH CHECK (backend.allowed('pair_time'::text, to_jsonb(pair_time.*), true));
CREATE POLICY scoped_delete ON deanery.person FOR DELETE TO public USING (backend.allowed('person'::text, to_jsonb(person.*), true));
CREATE POLICY scoped_insert ON deanery.person FOR INSERT TO public WITH CHECK (backend.allowed('person'::text, to_jsonb(person.*), true));
CREATE POLICY scoped_read ON deanery.person FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (person_id = ANY (( SELECT backend.visible_ids('person'::text, false) AS visible_ids)::integer[]))));
CREATE POLICY scoped_update ON deanery.person FOR UPDATE TO public USING (backend.allowed('person'::text, to_jsonb(person.*), true)) WITH CHECK (backend.allowed('person'::text, to_jsonb(person.*), true));
CREATE POLICY scoped_delete ON deanery."position" FOR DELETE TO public USING (backend.allowed('position'::text, to_jsonb("position".*), true));
CREATE POLICY scoped_insert ON deanery."position" FOR INSERT TO public WITH CHECK (backend.allowed('position'::text, to_jsonb("position".*), true));
CREATE POLICY scoped_read ON deanery."position" FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery."position" FOR UPDATE TO public USING (backend.allowed('position'::text, to_jsonb("position".*), true)) WITH CHECK (backend.allowed('position'::text, to_jsonb("position".*), true));
CREATE POLICY scoped_delete ON deanery.practice_placement FOR DELETE TO public USING (backend.allowed('practice_placement'::text, to_jsonb(practice_placement.*), true));
CREATE POLICY scoped_insert ON deanery.practice_placement FOR INSERT TO public WITH CHECK (backend.allowed('practice_placement'::text, to_jsonb(practice_placement.*), true));
CREATE POLICY scoped_read ON deanery.practice_placement FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('practice_placement'::text, to_jsonb(practice_placement.*), false)));
CREATE POLICY scoped_update ON deanery.practice_placement FOR UPDATE TO public USING (backend.allowed('practice_placement'::text, to_jsonb(practice_placement.*), true)) WITH CHECK (backend.allowed('practice_placement'::text, to_jsonb(practice_placement.*), true));
CREATE POLICY scoped_delete ON deanery.request_status FOR DELETE TO public USING (( SELECT backend.is_admin() AS is_admin));
CREATE POLICY scoped_insert ON deanery.request_status FOR INSERT TO public WITH CHECK (backend.allowed('request_status'::text, to_jsonb(request_status.*), true));
CREATE POLICY scoped_read ON deanery.request_status FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.request_status FOR UPDATE TO public USING (backend.allowed('request_status'::text, to_jsonb(request_status.*), true)) WITH CHECK (backend.allowed('request_status'::text, to_jsonb(request_status.*), true));
CREATE POLICY scoped_delete ON deanery.schedule_slot FOR DELETE TO public USING (backend.allowed('schedule_slot'::text, to_jsonb(schedule_slot.*), true));
CREATE POLICY scoped_insert ON deanery.schedule_slot FOR INSERT TO public WITH CHECK (backend.allowed('schedule_slot'::text, to_jsonb(schedule_slot.*), true));
CREATE POLICY scoped_read ON deanery.schedule_slot FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (slot_id = ANY (( SELECT backend.visible_ids('schedule_slot'::text, false) AS visible_ids)::integer[])) OR (assignment_id = ANY (( SELECT backend.visible_ids('teaching_assignment'::text, true) AS visible_ids)::integer[]))));
CREATE POLICY scoped_update ON deanery.schedule_slot FOR UPDATE TO public USING (backend.allowed('schedule_slot'::text, to_jsonb(schedule_slot.*), true)) WITH CHECK (backend.allowed('schedule_slot'::text, to_jsonb(schedule_slot.*), true));
CREATE POLICY scoped_delete ON deanery.scholarship FOR DELETE TO public USING (backend.allowed('scholarship'::text, to_jsonb(scholarship.*), true));
CREATE POLICY scoped_insert ON deanery.scholarship FOR INSERT TO public WITH CHECK (backend.allowed('scholarship'::text, to_jsonb(scholarship.*), true));
CREATE POLICY scoped_read ON deanery.scholarship FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('scholarship'::text, to_jsonb(scholarship.*), false)));
CREATE POLICY scoped_update ON deanery.scholarship FOR UPDATE TO public USING (backend.allowed('scholarship'::text, to_jsonb(scholarship.*), true)) WITH CHECK (backend.allowed('scholarship'::text, to_jsonb(scholarship.*), true));
CREATE POLICY scoped_delete ON deanery.scholarship_type FOR DELETE TO public USING (( SELECT backend.is_admin() AS is_admin));
CREATE POLICY scoped_insert ON deanery.scholarship_type FOR INSERT TO public WITH CHECK (backend.allowed('scholarship_type'::text, to_jsonb(scholarship_type.*), true));
CREATE POLICY scoped_read ON deanery.scholarship_type FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.scholarship_type FOR UPDATE TO public USING (backend.allowed('scholarship_type'::text, to_jsonb(scholarship_type.*), true)) WITH CHECK (backend.allowed('scholarship_type'::text, to_jsonb(scholarship_type.*), true));
CREATE POLICY scoped_delete ON deanery.score_band FOR DELETE TO public USING (backend.allowed('score_band'::text, to_jsonb(score_band.*), true));
CREATE POLICY scoped_insert ON deanery.score_band FOR INSERT TO public WITH CHECK (backend.allowed('score_band'::text, to_jsonb(score_band.*), true));
CREATE POLICY scoped_read ON deanery.score_band FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.score_band FOR UPDATE TO public USING (backend.allowed('score_band'::text, to_jsonb(score_band.*), true)) WITH CHECK (backend.allowed('score_band'::text, to_jsonb(score_band.*), true));
CREATE POLICY scoped_delete ON deanery.specialty FOR DELETE TO public USING (( SELECT backend.is_admin() AS is_admin));
CREATE POLICY scoped_insert ON deanery.specialty FOR INSERT TO public WITH CHECK (backend.allowed('specialty'::text, to_jsonb(specialty.*), true));
CREATE POLICY scoped_read ON deanery.specialty FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.specialty FOR UPDATE TO public USING (backend.allowed('specialty'::text, to_jsonb(specialty.*), true)) WITH CHECK (backend.allowed('specialty'::text, to_jsonb(specialty.*), true));
CREATE POLICY scoped_delete ON deanery.student FOR DELETE TO public USING (backend.allowed('student'::text, to_jsonb(student.*), true));
CREATE POLICY scoped_insert ON deanery.student FOR INSERT TO public WITH CHECK (backend.allowed('student'::text, to_jsonb(student.*), true));
CREATE POLICY scoped_read ON deanery.student FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (student_id = ANY (( SELECT backend.visible_ids('student'::text, false) AS visible_ids)::integer[]))));
CREATE POLICY scoped_update ON deanery.student FOR UPDATE TO public USING (backend.allowed('student'::text, to_jsonb(student.*), true)) WITH CHECK (backend.allowed('student'::text, to_jsonb(student.*), true));
CREATE POLICY scoped_delete ON deanery.student_contact FOR DELETE TO public USING (backend.allowed('student_contact'::text, to_jsonb(student_contact.*), true));
CREATE POLICY scoped_insert ON deanery.student_contact FOR INSERT TO public WITH CHECK (backend.allowed('student_contact'::text, to_jsonb(student_contact.*), true));
CREATE POLICY scoped_read ON deanery.student_contact FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR backend.allowed('student_contact'::text, to_jsonb(student_contact.*), false)));
CREATE POLICY scoped_update ON deanery.student_contact FOR UPDATE TO public USING (backend.allowed('student_contact'::text, to_jsonb(student_contact.*), true)) WITH CHECK (backend.allowed('student_contact'::text, to_jsonb(student_contact.*), true));
CREATE POLICY scoped_delete ON deanery.student_status FOR DELETE TO public USING (backend.allowed('student_status'::text, to_jsonb(student_status.*), true));
CREATE POLICY scoped_insert ON deanery.student_status FOR INSERT TO public WITH CHECK (backend.allowed('student_status'::text, to_jsonb(student_status.*), true));
CREATE POLICY scoped_read ON deanery.student_status FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.student_status FOR UPDATE TO public USING (backend.allowed('student_status'::text, to_jsonb(student_status.*), true)) WITH CHECK (backend.allowed('student_status'::text, to_jsonb(student_status.*), true));
CREATE POLICY scoped_delete ON deanery.study_form FOR DELETE TO public USING (backend.allowed('study_form'::text, to_jsonb(study_form.*), true));
CREATE POLICY scoped_insert ON deanery.study_form FOR INSERT TO public WITH CHECK (backend.allowed('study_form'::text, to_jsonb(study_form.*), true));
CREATE POLICY scoped_read ON deanery.study_form FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.study_form FOR UPDATE TO public USING (backend.allowed('study_form'::text, to_jsonb(study_form.*), true)) WITH CHECK (backend.allowed('study_form'::text, to_jsonb(study_form.*), true));
CREATE POLICY scoped_delete ON deanery.study_group FOR DELETE TO public USING (backend.allowed('study_group'::text, to_jsonb(study_group.*), true));
CREATE POLICY scoped_insert ON deanery.study_group FOR INSERT TO public WITH CHECK (backend.allowed('study_group'::text, to_jsonb(study_group.*), true));
CREATE POLICY scoped_read ON deanery.study_group FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (group_id = ANY (( SELECT backend.visible_ids('study_group'::text, false) AS visible_ids)::integer[])) OR (( SELECT d.institute_id
   FROM ((deanery.curriculum c
     JOIN deanery.study_program p USING (program_id))
     JOIN deanery.department d USING (department_id))
  WHERE (c.curriculum_id = study_group.curriculum_id)) = ANY (( SELECT backend.staff_institutes() AS staff_institutes)::integer[]))));
CREATE POLICY scoped_update ON deanery.study_group FOR UPDATE TO public USING (backend.allowed('study_group'::text, to_jsonb(study_group.*), true)) WITH CHECK (backend.allowed('study_group'::text, to_jsonb(study_group.*), true));
CREATE POLICY scoped_delete ON deanery.study_program FOR DELETE TO public USING (( SELECT backend.is_admin() AS is_admin));
CREATE POLICY scoped_insert ON deanery.study_program FOR INSERT TO public WITH CHECK (backend.allowed('study_program'::text, to_jsonb(study_program.*), true));
CREATE POLICY scoped_read ON deanery.study_program FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.study_program FOR UPDATE TO public USING (backend.allowed('study_program'::text, to_jsonb(study_program.*), true)) WITH CHECK (backend.allowed('study_program'::text, to_jsonb(study_program.*), true));
CREATE POLICY scoped_delete ON deanery.teacher FOR DELETE TO public USING (backend.allowed('teacher'::text, to_jsonb(teacher.*), true));
CREATE POLICY scoped_insert ON deanery.teacher FOR INSERT TO public WITH CHECK (backend.allowed('teacher'::text, to_jsonb(teacher.*), true));
CREATE POLICY scoped_read ON deanery.teacher FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (( SELECT backend.actor() AS actor) IS NOT NULL)));
CREATE POLICY scoped_update ON deanery.teacher FOR UPDATE TO public USING (backend.allowed('teacher'::text, to_jsonb(teacher.*), true)) WITH CHECK (backend.allowed('teacher'::text, to_jsonb(teacher.*), true));
CREATE POLICY scoped_delete ON deanery.teaching_assignment FOR DELETE TO public USING (backend.allowed('teaching_assignment'::text, to_jsonb(teaching_assignment.*), true));
CREATE POLICY scoped_insert ON deanery.teaching_assignment FOR INSERT TO public WITH CHECK (backend.allowed('teaching_assignment'::text, to_jsonb(teaching_assignment.*), true));
CREATE POLICY scoped_read ON deanery.teaching_assignment FOR SELECT TO public USING ((( SELECT backend.is_admin() AS is_admin) OR (assignment_id = ANY (( SELECT backend.visible_ids('teaching_assignment'::text, false) AS visible_ids)::integer[])) OR (group_id = ANY (( SELECT backend.visible_ids('study_group'::text, true) AS visible_ids)::integer[]))));
CREATE POLICY scoped_update ON deanery.teaching_assignment FOR UPDATE TO public USING (backend.allowed('teaching_assignment'::text, to_jsonb(teaching_assignment.*), true)) WITH CHECK (backend.allowed('teaching_assignment'::text, to_jsonb(teaching_assignment.*), true));
CREATE TRIGGER protected_change BEFORE INSERT OR DELETE OR UPDATE ON deanery.academic_leave FOR EACH ROW EXECUTE FUNCTION backend.protected_change();
CREATE TRIGGER order_parent_guard BEFORE UPDATE ON deanery.academic_order FOR EACH ROW EXECUTE FUNCTION backend.order_parent_guard();
CREATE TRIGGER protected_change BEFORE INSERT OR DELETE OR UPDATE ON deanery.academic_order FOR EACH ROW EXECUTE FUNCTION backend.protected_change();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.academic_term FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.classroom FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.curriculum FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.curriculum_item FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.department FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.discipline FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.education_level FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.funding_type FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER protected_change BEFORE INSERT OR DELETE OR UPDATE ON deanery.grade_sheet FOR EACH ROW EXECUTE FUNCTION backend.protected_change();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.institute FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER order_parent_guard BEFORE DELETE OR UPDATE ON deanery.order_student FOR EACH ROW EXECUTE FUNCTION backend.order_parent_guard();
CREATE TRIGGER protected_change BEFORE INSERT OR DELETE OR UPDATE ON deanery.order_student FOR EACH ROW EXECUTE FUNCTION backend.protected_change();
CREATE TRIGGER order_parent_guard BEFORE UPDATE ON deanery.order_type FOR EACH ROW EXECUTE FUNCTION backend.order_parent_guard();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery."position" FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.scholarship_type FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER parent_integrity AFTER INSERT OR DELETE OR UPDATE ON deanery.score_band FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.specialty FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER protected_change BEFORE INSERT OR UPDATE ON deanery.student FOR EACH ROW EXECUTE FUNCTION backend.protected_change();
CREATE TRIGGER student_capacity AFTER INSERT OR UPDATE OF group_id, status_id ON deanery.student FOR EACH ROW EXECUTE FUNCTION backend.student_capacity();
CREATE TRIGGER contact_parent_scope BEFORE INSERT OR UPDATE OF contact_person_id, student_id ON deanery.student_contact FOR EACH ROW EXECUTE FUNCTION backend.contact_parent_scope();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.student_status FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.study_form FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
CREATE TRIGGER parent_integrity AFTER UPDATE ON deanery.study_program FOR EACH ROW EXECUTE FUNCTION backend.parent_integrity();
DROP VIEW deanery.v_audit;
CREATE VIEW deanery.v_audit WITH (security_barrier=true) AS  SELECT al.audit_id,
    al.changed_at,
    al.table_name,
    al.record_key,
        CASE al.operation
            WHEN 'I'::bpchar THEN 'добавление'::text
            WHEN 'U'::bpchar THEN 'изменение'::text
            ELSE 'удаление'::text
        END AS operation,
    COALESCE('user:'::text || al.app_user_id::text, al.db_user::text)::character varying AS changed_by,
    NULL::text AS agent_request,
    d.column_name,
        CASE
            WHEN fields.allowed THEN d.old_value
            ELSE '[redacted]'::text
        END AS old_value,
        CASE
            WHEN fields.allowed THEN d.new_value
            ELSE '[redacted]'::text
        END AS new_value,
    al.app_user_id,
    al.agent_request_id
   FROM deanery.audit_log al
     JOIN deanery.audit_log_detail d USING (audit_id)
     CROSS JOIN LATERAL ( SELECT
                CASE al.table_name
                    WHEN 'grade'::text THEN d.column_name::text = ANY (ARRAY['grade_id'::text, 'sheet_id'::text, 'student_id'::text, 'points'::text, 'is_absent'::text])
                    WHEN 'grade_sheet'::text THEN d.column_name::text = ANY (ARRAY['sheet_id'::text, 'item_id'::text, 'group_id'::text, 'examiner_id'::text, 'stage'::text, 'sheet_kind'::text, 'issue_date'::text, 'exam_date'::text, 'closed_date'::text, 'status'::text])
                    WHEN 'student'::text THEN d.column_name::text = ANY (ARRAY['student_id'::text, 'group_id'::text, 'funding_type_id'::text, 'status_id'::text, 'enrollment_date'::text, 'needs_dormitory'::text])
                    WHEN 'attendance'::text THEN d.column_name::text = ANY (ARRAY['attendance_id'::text, 'slot_id'::text, 'student_id'::text, 'lesson_date'::text, 'is_present'::text])
                    WHEN 'academic_order'::text THEN d.column_name::text = ANY (ARRAY['order_id'::text, 'order_date'::text, 'order_type_id'::text, 'institute_id'::text, 'signed_by_id'::text, 'effective_date'::text])
                    WHEN 'order_student'::text THEN d.column_name::text = ANY (ARRAY['order_id'::text, 'student_id'::text, 'new_group_id'::text])
                    WHEN 'grade_correction'::text THEN d.column_name::text = ANY (ARRAY['correction_id','grade_id','old_points','old_is_absent','new_points','new_is_absent','requested_by_id','requested_at','status','decided_by_id','decided_at'])
                    WHEN 'academic_leave'::text THEN d.column_name::text = ANY (ARRAY['leave_id'::text, 'student_id'::text, 'reason_id'::text, 'order_id'::text, 'start_date'::text, 'end_date'::text, 'return_order_id'::text])
                    WHEN 'scholarship'::text THEN d.column_name::text = ANY (ARRAY['scholarship_id'::text, 'student_id'::text, 'scholarship_type_id'::text, 'order_id'::text, 'start_date'::text, 'end_date'::text])
                    WHEN 'curriculum'::text THEN d.column_name::text = ANY (ARRAY['curriculum_id'::text, 'program_id'::text, 'study_form_id'::text, 'start_year'::text, 'duration_years'::text, 'approved_date'::text])
                    WHEN 'curriculum_item'::text THEN d.column_name::text = ANY (ARRAY['item_id'::text, 'curriculum_id'::text, 'discipline_id'::text, 'semester'::text, 'control_type_id'::text, 'lecture_hours'::text, 'practice_hours'::text, 'lab_hours'::text, 'self_study_hours'::text, 'module_count'::text])
                    WHEN 'schedule_slot'::text THEN d.column_name::text = ANY (ARRAY['slot_id'::text, 'assignment_id'::text, 'classroom_id'::text, 'weekday'::text, 'pair_number'::text, 'week_parity'::text, 'term_id'::text, 'valid_from'::text, 'valid_to'::text])
                    WHEN 'teaching_assignment'::text THEN d.column_name::text = ANY (ARRAY['assignment_id'::text, 'item_id'::text, 'group_id'::text, 'teacher_id'::text, 'lesson_type_id'::text, 'subgroup'::text])
                    ELSE false
                END AS allowed) fields
  WHERE backend.audit_allowed(al.scope_institute_ids, al.scope_global);
