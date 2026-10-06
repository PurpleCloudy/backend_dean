-- =====================================================================
--  Представления (READ), функции, процедуры и триггеры
--  Выполнять после 01_schema.sql
-- =====================================================================
SET search_path TO deanery, public;

-- ---------------------------------------------------------------------
-- Вспомогательные функции: курс и семестр вычисляются, а не хранятся
-- (хранение курса нарушало бы 3НФ и устаревало бы каждый сентябрь)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_course(p_start_year INT, p_on DATE DEFAULT CURRENT_DATE)
RETURNS INT LANGUAGE sql IMMUTABLE AS $$
    SELECT EXTRACT(YEAR FROM p_on)::INT - p_start_year
         + CASE WHEN EXTRACT(MONTH FROM p_on) >= 9 THEN 1 ELSE 0 END;
$$;

CREATE OR REPLACE FUNCTION fn_semester(p_start_year INT, p_on DATE DEFAULT CURRENT_DATE)
RETURNS INT LANGUAGE sql IMMUTABLE AS $$
    SELECT (fn_course(p_start_year, p_on) - 1) * 2
         + CASE WHEN EXTRACT(MONTH FROM p_on) IN (9,10,11,12,1) THEN 1 ELSE 2 END;
$$;

-- Трудоёмкость в ЗЕТ: 1 ЗЕТ = 36 академических часов
CREATE OR REPLACE FUNCTION fn_credits(ci curriculum_item)
RETURNS NUMERIC LANGUAGE sql IMMUTABLE AS $$
    SELECT ROUND((ci.lecture_hours + ci.practice_hours + ci.lab_hours + ci.self_study_hours) / 36.0, 1);
$$;

CREATE OR REPLACE FUNCTION fn_full_name(p person)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT concat_ws(' ', p.last_name, p.first_name, p.middle_name);
$$;

-- Фактическое окончание академического отпуска: плановое или, при досрочном
-- выходе, день перед датой вступления в силу приказа о выходе.
-- Вычисляется, а не хранится: дата выхода уже есть в приказе (иначе — дубль и нарушение НФБК).
CREATE OR REPLACE FUNCTION fn_leave_actual_end(al academic_leave)
RETURNS DATE LANGUAGE sql STABLE AS $$
    SELECT COALESCE(LEAST(al.end_date, (SELECT o.effective_date - 1 FROM academic_order o
                                         WHERE o.order_id = al.return_order_id)), al.end_date);
$$;

-- Когда приказ должен быть исполнен для студента: дата вступления в силу,
-- а для приказа об академическом отпуске — не раньше начала самого отпуска.
CREATE OR REPLACE FUNCTION fn_order_due_date(p_order_id INT, p_student_id INT)
RETURNS DATE LANGUAGE sql STABLE AS $$
    SELECT GREATEST(o.effective_date,
                    (SELECT MAX(al.start_date) FROM academic_leave al
                     WHERE al.order_id = o.order_id AND al.student_id = p_student_id))
    FROM academic_order o WHERE o.order_id = p_order_id;
$$;

-- =====================================================================
-- ПРЕДСТАВЛЕНИЯ ДЛЯ ПРОСМОТРА
-- =====================================================================

-- Карточки студентов
CREATE OR REPLACE VIEW v_students AS
SELECT s.student_id,
       fn_full_name(p)            AS full_name,
       p.gender, p.birth_date, p.phone, p.email,
       s.record_book_number,
       g.name                     AS group_name,
       fn_course(c.start_year)    AS course,
       sp.code || ' ' || sp.name  AS specialty,
       pr.profile                 AS profile,
       sf.name                    AS study_form,
       ft.name                    AS funding,
       st.name                    AS status,
       f.short_name               AS institute,
       s.enrollment_date
FROM student s
JOIN person          p  ON p.person_id = s.person_id
JOIN study_group     g  ON g.group_id = s.group_id
JOIN curriculum      c  ON c.curriculum_id = g.curriculum_id
JOIN study_program   pr ON pr.program_id = c.program_id
JOIN specialty       sp ON sp.specialty_id = pr.specialty_id
JOIN department      d  ON d.department_id = pr.department_id
JOIN institute         f  ON f.institute_id = d.institute_id
JOIN study_form      sf ON sf.study_form_id = c.study_form_id
JOIN funding_type    ft ON ft.funding_type_id = s.funding_type_id
JOIN student_status  st ON st.status_id = s.status_id;

-- Учебные планы: дисциплины по семестрам с часами и ЗЕТ
CREATE OR REPLACE VIEW v_curriculum AS
SELECT sp.code || ' ' || sp.name AS specialty, pr.profile, sf.name AS study_form, c.start_year,
       ci.semester, dis.name AS discipline, dis.kind, ct.name AS control_type,
       ci.lecture_hours, ci.practice_hours, ci.lab_hours, ci.self_study_hours,
       ci.lecture_hours + ci.practice_hours + ci.lab_hours + ci.self_study_hours AS total_hours,
       fn_credits(ci) AS credits
FROM curriculum_item ci
JOIN curriculum    c   ON c.curriculum_id = ci.curriculum_id
JOIN study_program pr  ON pr.program_id = c.program_id
JOIN specialty     sp  ON sp.specialty_id = pr.specialty_id
JOIN study_form    sf  ON sf.study_form_id = c.study_form_id
JOIN discipline    dis ON dis.discipline_id = ci.discipline_id
JOIN control_type  ct  ON ct.control_type_id = ci.control_type_id;

-- Преподаватели
CREATE OR REPLACE VIEW v_teachers AS
SELECT e.employee_id,
       fn_full_name(p)       AS full_name,
       pos.title             AS position,
       ad.short_name         AS degree,
       at.name               AS academic_title,
       d.short_name          AS department,
       f.short_name          AS institute,
       e.employment_rate,
       date_part('year', age(CURRENT_DATE, t.teaching_since))::INT AS teaching_experience_years,
       p.phone, p.email
FROM teacher t
JOIN employee        e   ON e.employee_id = t.employee_id
JOIN person          p   ON p.person_id = e.person_id
JOIN position        pos ON pos.position_id = e.position_id
JOIN department      d   ON d.department_id = t.department_id
JOIN institute         f   ON f.institute_id = d.institute_id
LEFT JOIN academic_degree ad ON ad.degree_id = t.degree_id
LEFT JOIN academic_title  at ON at.title_id  = t.title_id
WHERE e.dismissal_date IS NULL;

-- Все сотрудники (включая деканат, администрацию, преподавателей)
CREATE OR REPLACE VIEW v_employees AS
SELECT e.employee_id,
       e.personnel_number,
       fn_full_name(p)  AS full_name,
       pos.title        AS position,
       pos.category,
       COALESCE(d.short_name, 'Деканат ' || fs.short_name, 'Дирекция ' || fd.short_name) AS unit,
       e.employment_rate, e.hire_date, e.dismissal_date,
       p.phone, p.email
FROM employee e
JOIN person   p   ON p.person_id = e.person_id
JOIN position pos ON pos.position_id = e.position_id
LEFT JOIN teacher            t  ON t.employee_id = e.employee_id
LEFT JOIN department         d  ON d.department_id = t.department_id
LEFT JOIN dean_office_staff  ds ON ds.employee_id = e.employee_id
LEFT JOIN institute            fs ON fs.institute_id = ds.institute_id
LEFT JOIN institute            fd ON fd.director_id = e.employee_id;

-- Группы с курсом, куратором, старостой и численностью
CREATE OR REPLACE VIEW v_groups AS
SELECT g.group_id, g.name AS group_name,
       sp.code || ' ' || sp.name       AS specialty,
       pr.profile                      AS profile,
       sf.name                         AS study_form,
       c.start_year                    AS admission_year,
       fn_course(c.start_year)         AS course,
       fn_semester(c.start_year)       AS current_semester,
       fn_full_name(pc)                AS curator,
       fn_full_name(ph)                AS headman,
       COUNT(s.student_id) FILTER (WHERE st.is_active)     AS active_students,
       COUNT(s.student_id) FILTER (WHERE s.funding_type_id = 1 AND st.is_active) AS budget_students
FROM study_group g
JOIN curriculum  c  ON c.curriculum_id = g.curriculum_id
JOIN study_program pr ON pr.program_id = c.program_id
JOIN specialty   sp ON sp.specialty_id = pr.specialty_id
JOIN study_form  sf ON sf.study_form_id = c.study_form_id
LEFT JOIN employee ec ON ec.employee_id = g.curator_id
LEFT JOIN person   pc ON pc.person_id = ec.person_id
LEFT JOIN student  sh ON sh.student_id = g.headman_id
LEFT JOIN person   ph ON ph.person_id = sh.person_id
LEFT JOIN student  s  ON s.group_id = g.group_id
LEFT JOIN student_status st ON st.status_id = s.status_id
WHERE NOT g.is_archived
GROUP BY g.group_id, sp.code, sp.name, pr.profile, sf.name, c.start_year, pc.person_id, ph.person_id;

-- ---------------------------------------------------------------------
-- Расписание
-- ---------------------------------------------------------------------

-- Период, когда идёт занятие: весь семестр или его часть
CREATE OR REPLACE FUNCTION fn_slot_period(ss schedule_slot) RETURNS DATERANGE
LANGUAGE sql STABLE AS $$
    SELECT daterange(COALESCE(ss.valid_from, t.start_date), COALESCE(ss.valid_to, t.end_date), '[]')
    FROM academic_term t WHERE t.term_id = ss.term_id;
$$;

-- Номер учебной недели внутри семестра (1-я — нечётная)
CREATE OR REPLACE FUNCTION fn_week_number(p_term_start DATE, p_day DATE) RETURNS INT
LANGUAGE sql IMMUTABLE AS $$
    SELECT (date_trunc('week', p_day)::DATE - date_trunc('week', p_term_start)::DATE) / 7 + 1;
$$;

-- Недельная сетка
CREATE OR REPLACE VIEW v_schedule AS
SELECT t.name AS term, g.name AS group_name,
       ss.weekday,
       (ARRAY['Пн','Вт','Ср','Чт','Пт','Сб'])[ss.weekday] AS weekday_name,
       ss.pair_number, pt.start_time, pt.end_time,
       CASE ss.week_parity WHEN 'odd' THEN 'нечётная'
                           WHEN 'even' THEN 'чётная' ELSE 'каждая' END AS week,
       dis.name        AS discipline,
       lt.name         AS lesson_type,
       fn_full_name(p) AS teacher,
       cr.building || '-' || cr.room_number AS classroom,
       ta.subgroup,
       lower(fn_slot_period(ss)) AS valid_from,
       upper(fn_slot_period(ss)) - 1 AS valid_to
FROM schedule_slot ss
JOIN academic_term       t   ON t.term_id = ss.term_id
JOIN pair_time           pt  ON pt.pair_number = ss.pair_number
JOIN teaching_assignment ta  ON ta.assignment_id = ss.assignment_id
JOIN study_group         g   ON g.group_id = ta.group_id
JOIN curriculum_item     ci  ON ci.item_id = ta.item_id
JOIN discipline          dis ON dis.discipline_id = ci.discipline_id
JOIN lesson_type         lt  ON lt.lesson_type_id = ta.lesson_type_id
JOIN employee            e   ON e.employee_id = ta.teacher_id
JOIN person              p   ON p.person_id = e.person_id
JOIN classroom           cr  ON cr.classroom_id = ss.classroom_id;

-- Расписание на весь семестр по датам: недельная сетка, развёрнутая в календарь
-- с учётом чётности недель, периода действия занятия и праздников.
-- Пример: SELECT * FROM v_schedule_calendar WHERE group_name = 'ИДБ-23-11' ORDER BY lesson_date, pair_number;
CREATE OR REPLACE VIEW v_schedule_calendar AS
SELECT d::DATE AS lesson_date,
       (ARRAY['Пн','Вт','Ср','Чт','Пт','Сб'])[ss.weekday] AS weekday_name,
       fn_week_number(t.start_date, d::DATE) AS week_no,
       CASE WHEN fn_week_number(t.start_date, d::DATE) % 2 = 1 THEN 'нечётная' ELSE 'чётная' END AS week,
       ss.pair_number, pt.start_time, pt.end_time,
       g.name          AS group_name,
       dis.name        AS discipline,
       lt.name         AS lesson_type,
       fn_full_name(p) AS teacher,
       cr.building || '-' || cr.room_number AS classroom,
       ta.subgroup,
       t.name          AS term,
       ss.slot_id
FROM schedule_slot ss
JOIN academic_term       t   ON t.term_id = ss.term_id
CROSS JOIN LATERAL generate_series(lower(fn_slot_period(ss)), upper(fn_slot_period(ss)) - 1, INTERVAL '1 day') AS d
JOIN pair_time           pt  ON pt.pair_number = ss.pair_number
JOIN teaching_assignment ta  ON ta.assignment_id = ss.assignment_id
JOIN study_group         g   ON g.group_id = ta.group_id
JOIN curriculum_item     ci  ON ci.item_id = ta.item_id
JOIN discipline          dis ON dis.discipline_id = ci.discipline_id
JOIN lesson_type         lt  ON lt.lesson_type_id = ta.lesson_type_id
JOIN employee            e   ON e.employee_id = ta.teacher_id
JOIN person              p   ON p.person_id = e.person_id
JOIN classroom           cr  ON cr.classroom_id = ss.classroom_id
WHERE EXTRACT(ISODOW FROM d) = ss.weekday
  AND (ss.week_parity = 'every'
       OR (ss.week_parity = 'odd'  AND fn_week_number(t.start_date, d::DATE) % 2 = 1)
       OR (ss.week_parity = 'even' AND fn_week_number(t.start_date, d::DATE) % 2 = 0))
  AND NOT EXISTS (SELECT 1 FROM holiday h WHERE h.holiday_date = d::DATE);

-- Сверка расписания с учебным планом: сколько часов запланировано и сколько
-- реально выходит по календарю (1 пара = 2 академических часа)
CREATE OR REPLACE VIEW v_schedule_hours AS
SELECT g.name AS group_name, dis.name AS discipline, lt.name AS lesson_type,
       fn_full_name(p) AS teacher,
       CASE lt.name WHEN 'Лекция' THEN ci.lecture_hours
                    WHEN 'Практика' THEN ci.practice_hours
                    ELSE ci.lab_hours END AS planned_hours,
       2 * (SELECT COUNT(*) FROM v_schedule_calendar c
            JOIN schedule_slot s ON s.slot_id = c.slot_id
            WHERE s.assignment_id = ta.assignment_id) AS scheduled_hours
FROM teaching_assignment ta
JOIN study_group     g   ON g.group_id = ta.group_id
JOIN curriculum_item ci  ON ci.item_id = ta.item_id
JOIN discipline      dis ON dis.discipline_id = ci.discipline_id
JOIN lesson_type     lt  ON lt.lesson_type_id = ta.lesson_type_id
JOIN employee        e   ON e.employee_id = ta.teacher_id
JOIN person          p   ON p.person_id = e.person_id
WHERE EXISTS (SELECT 1 FROM schedule_slot s WHERE s.assignment_id = ta.assignment_id);

-- ---------------------------------------------------------------------
-- Успеваемость (модульно-рейтинговая система)
-- ---------------------------------------------------------------------

-- Оценка 3/4/5 по баллам (по шкале score_band); NULL — баллов нет
CREATE OR REPLACE FUNCTION fn_mark(p_points INT) RETURNS SMALLINT
LANGUAGE sql STABLE AS $$
    SELECT mark FROM score_band WHERE p_points BETWEEN min_points AND max_points;
$$;

-- Результат словами с учётом формы контроля
CREATE OR REPLACE FUNCTION fn_result_name(p_points INT, p_absent BOOLEAN, p_graded BOOLEAN, p_final BOOLEAN)
RETURNS TEXT LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN p_absent THEN 'Неявка'
        WHEN p_points IS NULL AND NOT p_final THEN 'Не аттестован'
        WHEN p_points IS NULL AND p_graded THEN 'Неудовлетворительно'
        WHEN p_points IS NULL THEN 'Не зачтено'
        WHEN p_final AND NOT p_graded THEN 'Зачтено'
        ELSE (SELECT mark_name FROM score_band WHERE p_points BETWEEN min_points AND max_points)
    END;
$$;

-- Значение ячейки отчёта: баллы, «н/а» (не аттестован) или «неявка»
CREATE OR REPLACE FUNCTION fn_result_cell(p_points INT, p_absent BOOLEAN) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN p_absent THEN 'неявка' WHEN p_points IS NULL THEN 'н/а' ELSE p_points::TEXT END;
$$;

-- Все результаты: модули и итоговый контроль, все попытки
CREATE OR REPLACE VIEW v_grades AS
SELECT s.student_id, fn_full_name(p) AS student, g.name AS group_name,
       dis.name AS discipline, ci.semester,
       CASE gs.stage WHEN 'module_1' THEN 'Модуль 1' WHEN 'module_2' THEN 'Модуль 2' ELSE ct.name END AS stage,
       gr.points,
       fn_mark(gr.points) AS mark,
       fn_result_name(gr.points, gr.is_absent, ct.is_graded, gs.stage = 'final') AS result,
       gr.points IS NOT NULL AS passed,
       gs.sheet_number, gs.sheet_kind, gs.exam_date,
       fn_full_name(pe) AS examiner
FROM grade gr
JOIN grade_sheet     gs  ON gs.sheet_id = gr.sheet_id
JOIN curriculum_item ci  ON ci.item_id = gs.item_id
JOIN discipline      dis ON dis.discipline_id = ci.discipline_id
JOIN control_type    ct  ON ct.control_type_id = ci.control_type_id
JOIN student         s   ON s.student_id = gr.student_id
JOIN person          p   ON p.person_id = s.person_id
JOIN study_group     g   ON g.group_id = s.group_id
JOIN employee        e   ON e.employee_id = gs.examiner_id
JOIN person          pe  ON pe.person_id = e.person_id
WHERE gs.status <> 'cancelled';

-- Последний (действующий) результат студента по каждому этапу дисциплины.
-- Ключ — дисциплина + семестр, а не строка конкретного учебного плана: если студента
-- перевели в группу другого года набора (например, после академа или восстановления),
-- его прежние результаты по той же дисциплине того же семестра засчитываются.
CREATE OR REPLACE VIEW v_last_result AS
SELECT DISTINCT ON (gr.student_id, ci.discipline_id, ci.semester, gs.stage)
       gr.student_id, ci.discipline_id, ci.semester, gs.item_id, gs.stage,
       gr.points, gr.is_absent, gs.sheet_id, gs.exam_date
FROM grade gr
JOIN grade_sheet     gs ON gs.sheet_id = gr.sheet_id AND gs.status <> 'cancelled'
JOIN curriculum_item ci ON ci.item_id = gs.item_id
ORDER BY gr.student_id, ci.discipline_id, ci.semester, gs.stage, gs.exam_date DESC NULLS LAST, gs.sheet_id DESC;

-- УСПЕВАЕМОСТЬ СТУДЕНТА: по каждой дисциплине его учебного плана (до текущего семестра)
-- баллы за модуль 1, модуль 2 и за зачёт или экзамен.
-- «—» — такого контроля у дисциплины нет; пусто — ещё не выставлено;
-- «н/а» — не аттестован (меньше 25 баллов); «неявка».
-- Пример: SELECT * FROM v_performance WHERE student LIKE 'Иванов%' ORDER BY semester, discipline;
CREATE OR REPLACE VIEW v_performance AS
SELECT s.student_id, fn_full_name(p) AS student, g.name AS group_name,
       ci.semester, dis.name AS discipline,
       CASE WHEN ci.module_count < 1 THEN '—' WHEN m1.student_id IS NOT NULL THEN fn_result_cell(m1.points, m1.is_absent) END AS module_1,
       CASE WHEN ci.module_count < 2 THEN '—' WHEN m2.student_id IS NOT NULL THEN fn_result_cell(m2.points, m2.is_absent) END AS module_2,
       CASE WHEN ct.is_exam          THEN '—' WHEN f.student_id  IS NOT NULL THEN fn_result_cell(f.points, f.is_absent)  END AS credit,
       CASE WHEN NOT ct.is_exam      THEN '—' WHEN f.student_id  IS NOT NULL THEN fn_result_cell(f.points, f.is_absent)  END AS exam,
       ct.name AS control_type,
       CASE WHEN f.student_id IS NOT NULL
            THEN fn_result_name(f.points, f.is_absent, ct.is_graded, TRUE) END AS final_result
FROM student s
JOIN person          p   ON p.person_id = s.person_id
JOIN study_group     g   ON g.group_id = s.group_id
JOIN curriculum      c   ON c.curriculum_id = g.curriculum_id
JOIN curriculum_item ci  ON ci.curriculum_id = c.curriculum_id
                        AND ci.semester <= fn_semester(c.start_year)
JOIN discipline      dis ON dis.discipline_id = ci.discipline_id
JOIN control_type    ct  ON ct.control_type_id = ci.control_type_id
LEFT JOIN v_last_result m1 ON m1.student_id = s.student_id AND m1.discipline_id = ci.discipline_id
                          AND m1.semester = ci.semester AND m1.stage = 'module_1'
LEFT JOIN v_last_result m2 ON m2.student_id = s.student_id AND m2.discipline_id = ci.discipline_id
                          AND m2.semester = ci.semester AND m2.stage = 'module_2'
LEFT JOIN v_last_result f  ON f.student_id  = s.student_id AND f.discipline_id  = ci.discipline_id
                          AND f.semester  = ci.semester AND f.stage  = 'final';

-- Академические задолженности: итоговый контроль сдавали, но положительного результата нет
CREATE OR REPLACE VIEW v_debtors AS
SELECT s.student_id, fn_full_name(p) AS student, g.name AS group_name,
       dis.name AS discipline, ci.semester, ct.name AS control_type,
       COUNT(*) AS attempts,
       (array_agg(fn_result_name(gr.points, gr.is_absent, ct.is_graded, TRUE)
                  ORDER BY gs.exam_date DESC NULLS LAST))[1] AS last_result
FROM grade gr
JOIN grade_sheet     gs  ON gs.sheet_id = gr.sheet_id AND gs.status <> 'cancelled' AND gs.stage = 'final'
JOIN curriculum_item ci  ON ci.item_id = gs.item_id
JOIN discipline      dis ON dis.discipline_id = ci.discipline_id
JOIN control_type    ct  ON ct.control_type_id = ci.control_type_id
JOIN student         s   ON s.student_id = gr.student_id
JOIN student_status  st  ON st.status_id = s.status_id AND st.is_active
JOIN person          p   ON p.person_id = s.person_id
JOIN study_group     g   ON g.group_id = s.group_id
GROUP BY s.student_id, p.person_id, g.name, dis.name, ci.semester, ct.name
HAVING NOT bool_or(gr.points IS NOT NULL);

-- Кандидаты на отчисление: не сдана комиссия (3 попытки) или три и более задолженности.
-- Решение принимает деканат; представление только собирает список.
CREATE OR REPLACE VIEW v_expulsion_risk AS
SELECT d.student_id, d.student, d.group_name,
       COUNT(*)        AS debts,
       MAX(d.attempts) AS max_attempts,
       string_agg(d.discipline || ' (' || d.attempts || ' попыт' ||
                  CASE WHEN d.attempts = 1 THEN 'ка' ELSE 'ки' END || ')', ', ' ORDER BY d.discipline) AS disciplines,
       CASE WHEN MAX(d.attempts) >= 3 THEN 'Не сдана комиссия'
            ELSE 'Три и более задолженности' END AS reason
FROM v_debtors d
GROUP BY d.student_id, d.student, d.group_name
HAVING MAX(d.attempts) >= 3 OR COUNT(*) >= 3;

-- СВОДКА ПО СТУДЕНТАМ: одна строка на студента — статус, средний балл, долги,
-- посещаемость, стипендии и категория (отличник, хорошист, с долгами, кандидат на отчисление …).
-- Пример: SELECT category, count(*) FROM v_student_summary GROUP BY 1 ORDER BY 2 DESC;
CREATE OR REPLACE VIEW v_student_summary AS
WITH marks AS (
    SELECT lr.student_id,
           ROUND(AVG(lr.points), 1)                                    AS avg_points,
           MIN(fn_mark(lr.points)) FILTER (WHERE ct.is_graded)         AS min_mark,
           COUNT(*)                                                    AS results
    FROM v_last_result lr
    JOIN curriculum_item ci ON ci.item_id = lr.item_id
    JOIN control_type    ct ON ct.control_type_id = ci.control_type_id
    WHERE lr.stage = 'final'
    GROUP BY lr.student_id),
debts AS (SELECT student_id, COUNT(*) AS debts FROM v_debtors GROUP BY student_id),
att AS (
    SELECT student_id, ROUND(100.0 * AVG(is_present::INT), 1) AS attendance_pct
    FROM attendance GROUP BY student_id),
sch AS (
    SELECT sc.student_id, string_agg(stp.name, ', ' ORDER BY stp.amount DESC) AS scholarships
    FROM scholarship sc JOIN scholarship_type stp ON stp.scholarship_type_id = sc.scholarship_type_id
    WHERE CURRENT_DATE BETWEEN sc.start_date AND sc.end_date
    GROUP BY sc.student_id)
SELECT s.student_id, fn_full_name(p) AS student, s.record_book_number, g.name AS group_name,
       fn_course(c.start_year) AS course, st.name AS status, ft.name AS funding,
       m.avg_points, COALESCE(d.debts, 0) AS debts, a.attendance_pct, sch.scholarships,
       (g.headman_id = s.student_id) AS is_headman, s.is_foreign, s.needs_dormitory,
       CASE WHEN NOT st.is_active                     THEN st.name
            WHEN er.student_id IS NOT NULL            THEN 'Кандидат на отчисление'
            WHEN d.debts > 0                          THEN 'Есть задолженности'
            WHEN m.results IS NULL                    THEN 'Ещё не сдавал сессию'
            WHEN m.min_mark = 5                       THEN 'Отличник'
            WHEN m.min_mark = 4                       THEN 'Хорошист'
            WHEN m.min_mark = 3                       THEN 'Есть тройки'
            ELSE 'Только зачёты' END              AS category
FROM student s
JOIN person          p  ON p.person_id = s.person_id
JOIN study_group     g  ON g.group_id = s.group_id
JOIN curriculum      c  ON c.curriculum_id = g.curriculum_id
JOIN student_status  st ON st.status_id = s.status_id
JOIN funding_type    ft ON ft.funding_type_id = s.funding_type_id
LEFT JOIN marks      m  ON m.student_id = s.student_id
LEFT JOIN debts      d  ON d.student_id = s.student_id
LEFT JOIN att        a  ON a.student_id = s.student_id
LEFT JOIN sch           ON sch.student_id = s.student_id
LEFT JOIN v_expulsion_risk er ON er.student_id = s.student_id;

-- Рейтинг: средний балл по итоговому контролю, средняя оценка и число долгов
CREATE OR REPLACE VIEW v_student_rating AS
SELECT s.student_id, fn_full_name(p) AS student, g.name AS group_name,
       ROUND(AVG(lr.points), 1)                        AS avg_points,
       ROUND(AVG(fn_mark(lr.points)) FILTER (WHERE ct.is_graded), 2) AS avg_mark,
       COUNT(*) FILTER (WHERE fn_mark(lr.points) = 5 AND ct.is_graded) AS excellent_count,
       (SELECT COUNT(*) FROM v_debtors d WHERE d.student_id = s.student_id) AS debts
FROM student s
JOIN person p          ON p.person_id = s.person_id
JOIN study_group g     ON g.group_id = s.group_id
JOIN student_status st ON st.status_id = s.status_id AND st.is_active
LEFT JOIN v_last_result lr   ON lr.student_id = s.student_id AND lr.stage = 'final'
LEFT JOIN curriculum_item ci ON ci.item_id = lr.item_id
LEFT JOIN control_type ct    ON ct.control_type_id = ci.control_type_id
GROUP BY s.student_id, p.person_id, g.name;

-- Действующие стипендии
CREATE OR REPLACE VIEW v_active_scholarships AS
SELECT fn_full_name(p) AS student, g.name AS group_name,
       stp.name AS scholarship, stp.amount,
       sc.start_date, sc.end_date, o.order_number
FROM scholarship sc
JOIN scholarship_type stp ON stp.scholarship_type_id = sc.scholarship_type_id
JOIN academic_order   o   ON o.order_id = sc.order_id
JOIN student          s   ON s.student_id = sc.student_id
JOIN person           p   ON p.person_id = s.person_id
JOIN study_group      g   ON g.group_id = s.group_id
WHERE CURRENT_DATE BETWEEN sc.start_date AND sc.end_date;

-- Учебная нагрузка преподавателей (часы из учебного плана по виду занятия)
CREATE OR REPLACE VIEW v_teacher_load AS
SELECT fn_full_name(p) AS teacher, d.short_name AS department,
       SUM(CASE lt.name WHEN 'Лекция'       THEN ci.lecture_hours
                        WHEN 'Практика'     THEN ci.practice_hours
                        WHEN 'Лабораторная' THEN ci.lab_hours ELSE 0 END) AS total_hours,
       COUNT(DISTINCT ta.group_id)      AS groups,
       COUNT(DISTINCT ci.discipline_id) AS disciplines
FROM teaching_assignment ta
JOIN curriculum_item ci ON ci.item_id = ta.item_id
JOIN lesson_type     lt ON lt.lesson_type_id = ta.lesson_type_id
JOIN teacher         t  ON t.employee_id = ta.teacher_id
JOIN department      d  ON d.department_id = t.department_id
JOIN employee        e  ON e.employee_id = t.employee_id
JOIN person          p  ON p.person_id = e.person_id
GROUP BY p.person_id, d.short_name;

-- Очередь заявок на справки со сроком исполнения
CREATE OR REPLACE VIEW v_document_queue AS
SELECT dr.request_id, fn_full_name(p) AS student, g.name AS group_name,
       dt.name AS document, dr.copies, dr.purpose, rs.name AS status,
       dr.created_at,
       (dr.created_at + dt.processing_days * INTERVAL '1 day')::DATE AS due_date,
       fn_full_name(pe) AS processed_by
FROM document_request dr
JOIN document_type  dt ON dt.document_type_id = dr.document_type_id
JOIN request_status rs ON rs.request_status_id = dr.request_status_id
JOIN student        s  ON s.student_id = dr.student_id
JOIN person         p  ON p.person_id = s.person_id
JOIN study_group    g  ON g.group_id = s.group_id
LEFT JOIN employee  e  ON e.employee_id = dr.processed_by_id
LEFT JOIN person    pe ON pe.person_id = e.person_id
WHERE NOT rs.is_final;

-- Посещаемость по студентам
CREATE OR REPLACE VIEW v_attendance_stats AS
SELECT fn_full_name(p) AS student, g.name AS group_name,
       COUNT(*) AS lessons,
       COUNT(*) FILTER (WHERE NOT a.is_present) AS absences,
       ROUND(100.0 * COUNT(*) FILTER (WHERE a.is_present) / COUNT(*), 1) AS attendance_pct
FROM attendance a
JOIN student     s ON s.student_id = a.student_id
JOIN person      p ON p.person_id = s.person_id
JOIN study_group g ON g.group_id = s.group_id
GROUP BY p.person_id, g.name;

-- История приказов по студенту: исполнен или ждёт даты вступления в силу
CREATE OR REPLACE VIEW v_student_orders AS
SELECT os.student_id, fn_full_name(p) AS student, s.record_book_number, o.order_number, o.order_date,
       o.effective_date, ot.name AS order_type, o.title, os.reason, ng.name AS new_group,
       CASE WHEN os.applied_at IS NOT NULL THEN 'Исполнен'
            ELSE 'Запланирован на ' || to_char(fn_order_due_date(os.order_id, os.student_id), 'DD.MM.YYYY') END AS state,
       os.applied_at
FROM order_student os
JOIN academic_order o  ON o.order_id = os.order_id
JOIN order_type     ot ON ot.order_type_id = o.order_type_id
JOIN student        s  ON s.student_id = os.student_id
JOIN person         p  ON p.person_id = s.person_id
LEFT JOIN study_group ng ON ng.group_id = os.new_group_id;


-- Академические отпуска: действующие и завершённые.
-- end_date — плановое окончание, actual_end_date — фактическое (при досрочном
-- выходе — день перед выходом; остаток планового периода свободен).
CREATE OR REPLACE VIEW v_academic_leaves AS
SELECT al.student_id, fn_full_name(p) AS student, g.name AS group_name, lr.name AS reason,
       al.start_date, al.end_date, fn_leave_actual_end(al) AS actual_end_date,
       o.order_number AS order_number,
       ro.order_number AS return_order_number,
       ro.effective_date AS returned_on,
       CASE WHEN ro.effective_date <= CURRENT_DATE
                THEN CASE WHEN ro.effective_date <= al.end_date THEN 'Вышел досрочно' ELSE 'Вышел' END
            WHEN ro.order_id IS NOT NULL
                THEN 'В отпуске, выход назначен на ' || to_char(ro.effective_date, 'DD.MM.YYYY')
            WHEN CURRENT_DATE > al.end_date THEN 'Срок истёк, нет приказа о выходе'
            WHEN CURRENT_DATE >= al.start_date THEN 'В отпуске'
            ELSE 'Предстоит' END AS state,
       CASE WHEN ro.effective_date <= CURRENT_DATE THEN 0
            ELSE GREATEST(fn_leave_actual_end(al) - GREATEST(CURRENT_DATE, al.start_date - 1), 0) END AS days_left
FROM academic_leave al
JOIN leave_reason   lr ON lr.reason_id = al.reason_id
JOIN academic_order o  ON o.order_id = al.order_id
LEFT JOIN academic_order ro ON ro.order_id = al.return_order_id
JOIN student        s  ON s.student_id = al.student_id
JOIN person         p  ON p.person_id = s.person_id
JOIN study_group    g  ON g.group_id = s.group_id;

-- Практики студентов
CREATE OR REPLACE VIEW v_practice AS
SELECT fn_full_name(p) AS student, g.name AS group_name, dis.name AS practice, ci.semester,
       org.name AS organization, fn_full_name(pt) AS supervisor,
       concat_ws(' ', oc.last_name, oc.first_name, oc.middle_name) || ', ' || oc.job_title AS org_supervisor,
       pp.start_date, pp.end_date, o.order_number
FROM practice_placement pp
JOIN curriculum_item ci  ON ci.item_id = pp.item_id
JOIN discipline      dis ON dis.discipline_id = ci.discipline_id
JOIN organization_contact oc ON oc.org_contact_id = pp.org_contact_id
JOIN organization    org ON org.organization_id = oc.organization_id
JOIN student         s   ON s.student_id = pp.student_id
JOIN person          p   ON p.person_id = s.person_id
JOIN study_group     g   ON g.group_id = s.group_id
JOIN employee        et  ON et.employee_id = pp.supervisor_id
JOIN person          pt  ON pt.person_id = et.person_id
LEFT JOIN academic_order o ON o.order_id = pp.order_id;

-- Курсовые работы и ВКР
CREATE OR REPLACE VIEW v_academic_works AS
SELECT fn_full_name(p) AS student, g.name AS group_name,
       CASE dis.kind WHEN 'coursework' THEN 'Курсовая работа' ELSE 'ВКР' END AS work_kind,
       dis.name AS discipline, ci.semester, aw.topic,
       fn_full_name(ps) AS supervisor, fn_full_name(pr) AS reviewer, o.order_number
FROM academic_work aw
JOIN curriculum_item ci  ON ci.item_id = aw.item_id
JOIN discipline      dis ON dis.discipline_id = ci.discipline_id
JOIN student         s   ON s.student_id = aw.student_id
JOIN person          p   ON p.person_id = s.person_id
JOIN study_group     g   ON g.group_id = s.group_id
JOIN employee        es  ON es.employee_id = aw.supervisor_id
JOIN person          ps  ON ps.person_id = es.person_id
LEFT JOIN employee   er  ON er.employee_id = aw.reviewer_id
LEFT JOIN person     pr  ON pr.person_id = er.person_id
LEFT JOIN academic_order o ON o.order_id = aw.order_id;

-- Контакты родителей
CREATE OR REPLACE VIEW v_student_contacts AS
SELECT fn_full_name(p) AS student, g.name AS group_name, cr.name AS relation,
       concat_ws(' ', cp.last_name, cp.first_name, cp.middle_name) AS contact,
       cp.phone, cp.email, sc.is_emergency
FROM student_contact sc
JOIN contact_person   cp ON cp.contact_person_id = sc.contact_person_id
JOIN contact_relation cr ON cr.relation_id = sc.relation_id
JOIN student          s  ON s.student_id = sc.student_id
JOIN person           p  ON p.person_id = s.person_id
JOIN study_group      g  ON g.group_id = s.group_id;

-- Журнал изменений в читаемом виде: кто, когда, что поменял, по какому запросу агента
CREATE OR REPLACE VIEW v_audit AS
SELECT al.audit_id, al.changed_at, al.table_name, al.record_key,
       CASE al.operation WHEN 'I' THEN 'добавление' WHEN 'U' THEN 'изменение' ELSE 'удаление' END AS operation,
       COALESCE(u.login, al.db_user) AS changed_by,
       ar.request_text AS agent_request,
       d.column_name, d.old_value, d.new_value
FROM audit_log al
JOIN audit_log_detail d  ON d.audit_id = al.audit_id
LEFT JOIN app_user     u  ON u.user_id = al.app_user_id
LEFT JOIN agent_request ar ON ar.agent_request_id = al.agent_request_id;

-- Запланированные приказы: подписаны, но дата вступления в силу ещё не наступила.
-- В день вступления в силу их исполняет fn_apply_due_orders().
CREATE OR REPLACE VIEW v_pending_orders AS
SELECT os.student_id, fn_full_name(p) AS student, s.record_book_number, g.name AS group_name,
       st.name AS current_status, o.order_number, ot.code AS order_type_code, ot.name AS order_type,
       o.order_date, fn_order_due_date(os.order_id, os.student_id) AS due_date,
       fn_order_due_date(os.order_id, os.student_id) - CURRENT_DATE AS days_left,
       ng.name AS new_group, os.reason
FROM order_student os
JOIN academic_order o  ON o.order_id = os.order_id
JOIN order_type     ot ON ot.order_type_id = o.order_type_id
JOIN student        s  ON s.student_id = os.student_id
JOIN student_status st ON st.status_id = s.status_id
JOIN person         p  ON p.person_id = s.person_id
JOIN study_group    g  ON g.group_id = s.group_id
LEFT JOIN study_group ng ON ng.group_id = os.new_group_id
WHERE os.applied_at IS NULL;

-- Заявки на исправление оценок в закрытых ведомостях: что, кому, почему, кто решил
CREATE OR REPLACE VIEW v_grade_corrections AS
SELECT gc.correction_id, gs.sheet_number, d.name AS discipline, ci.semester,
       CASE gs.stage WHEN 'module_1' THEN 'Модуль 1' WHEN 'module_2' THEN 'Модуль 2' ELSE 'Итог' END AS stage,
       fn_full_name(p) AS student, s.record_book_number,
       fn_result_cell(gc.old_points, gc.old_is_absent) AS old_result,
       fn_result_cell(gc.new_points, gc.new_is_absent) AS new_result,
       fn_result_cell(gr.points, gr.is_absent) AS current_result,
       gc.reason, ru.login AS requested_by, gc.requested_at,
       CASE gc.status WHEN 'pending' THEN 'Ждёт решения' WHEN 'applied' THEN 'Одобрена и применена'
                      WHEN 'rejected' THEN 'Отклонена' ELSE 'Применяется' END AS status,
       du.login AS decided_by, gc.decided_at, gc.decision_comment
FROM grade_correction gc
JOIN grade           gr ON gr.grade_id = gc.grade_id
JOIN grade_sheet     gs ON gs.sheet_id = gr.sheet_id
JOIN curriculum_item ci ON ci.item_id = gs.item_id
JOIN discipline      d  ON d.discipline_id = ci.discipline_id
JOIN student         s  ON s.student_id = gr.student_id
JOIN person          p  ON p.person_id = s.person_id
JOIN app_user        ru ON ru.user_id = gc.requested_by_id
LEFT JOIN app_user   du ON du.user_id = gc.decided_by_id;

-- =====================================================================
-- ТРИГГЕРЫ ЦЕЛОСТНОСТИ
-- Правила, которые связывают несколько таблиц и не выражаются FK/CHECK
-- =====================================================================

-- --- 1. Строка учебного плана должна относиться к плану группы -------
-- (ведомости и учебные поручения)
CREATE OR REPLACE FUNCTION trg_item_matches_group() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM curriculum_item ci JOIN study_group g ON g.curriculum_id = ci.curriculum_id
        WHERE ci.item_id = NEW.item_id AND g.group_id = NEW.group_id)
    THEN
        RAISE EXCEPTION 'Дисциплина (строка плана %) не входит в учебный план группы %',
            NEW.item_id, (SELECT name FROM study_group WHERE group_id = NEW.group_id);
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER sheet_item_matches_group BEFORE INSERT OR UPDATE OF item_id, group_id ON grade_sheet
FOR EACH ROW EXECUTE FUNCTION trg_item_matches_group();

-- модульная ведомость — только если у дисциплины есть такой модуль
-- (у практики, курсовой и ВКР модулей нет, только итоговая оценка)
CREATE OR REPLACE FUNCTION trg_sheet_stage() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE v_modules INT;
BEGIN
    SELECT module_count INTO v_modules FROM curriculum_item WHERE item_id = NEW.item_id;
    IF (NEW.stage = 'module_1' AND v_modules < 1) OR (NEW.stage = 'module_2' AND v_modules < 2) THEN
        RAISE EXCEPTION 'У этой дисциплины % модул%, ведомость на % не нужна',
            v_modules, CASE v_modules WHEN 1 THEN 'ь' ELSE 'ей' END,
            CASE NEW.stage WHEN 'module_1' THEN 'модуль 1' ELSE 'модуль 2' END;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER sheet_stage BEFORE INSERT OR UPDATE OF stage, item_id ON grade_sheet
FOR EACH ROW EXECUTE FUNCTION trg_sheet_stage();
CREATE TRIGGER assignment_item_matches_group BEFORE INSERT OR UPDATE OF item_id, group_id ON teaching_assignment
FOR EACH ROW EXECUTE FUNCTION trg_item_matches_group();

-- --- 2. Оценки --------------------------------------------------------
-- • закрытую или отменённую ведомость менять нельзя. Единственное исключение —
--   одобренная заявка на исправление (grade_correction в статусе approved,
--   этот статус существует только внутри процедуры sp_decide_grade_correction):
--   оценку можно поставить ровно в значение из заявки;
-- • оценка должна подходить к форме контроля;
-- • студент должен быть из группы ведомости. Исключение — пересдача:
--   туда можно внести студента, у которого уже есть оценка по этой дисциплине
--   (например, если его перевели в другую группу с долгом).
CREATE OR REPLACE FUNCTION trg_grade_check() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_sheet    grade_sheet%ROWTYPE;
    v_group    INT;
    v_active   BOOLEAN;
    v_attempts BOOLEAN;
    v_passed   BOOLEAN;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT * INTO v_sheet FROM grade_sheet WHERE sheet_id = OLD.sheet_id;
        IF v_sheet.status <> 'open' THEN
            IF TG_OP = 'UPDATE' AND v_sheet.status = 'closed'
               AND NEW.sheet_id = OLD.sheet_id AND NEW.student_id = OLD.student_id
               AND EXISTS (SELECT 1 FROM grade_correction c
                           WHERE c.grade_id = OLD.grade_id AND c.status = 'approved'
                             AND c.new_points IS NOT DISTINCT FROM NEW.points
                             AND c.new_is_absent = NEW.is_absent)
            THEN
                RETURN NEW;          -- применение одобренной заявки
            END IF;
            RAISE EXCEPTION 'Ведомость % %: оценку можно исправить только по подтверждённой заявке — fn_request_grade_correction(''%'', ''<зачётка>'', баллы, неявка, ''основание''), затем решение директора или заместителя',
                v_sheet.sheet_number, CASE v_sheet.status WHEN 'closed' THEN 'закрыта' ELSE 'отменена' END, v_sheet.sheet_number;
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;

    SELECT * INTO v_sheet FROM grade_sheet WHERE sheet_id = NEW.sheet_id;
    IF v_sheet.status <> 'open' THEN
        RAISE EXCEPTION 'Ведомость % закрыта или отменена — изменение оценок запрещено', v_sheet.sheet_number;
    END IF;

    -- баллы должны попадать в шкалу (25–54); ниже минимума баллы не ставятся — «не аттестован»
    IF NEW.points IS NOT NULL AND fn_mark(NEW.points) IS NULL THEN
        RAISE EXCEPTION 'Баллы % вне шкалы: допустимо от % до %; ниже минимума ставится «не аттестован»',
            NEW.points, (SELECT MIN(min_points) FROM score_band), (SELECT MAX(max_points) FROM score_band);
    END IF;

    SELECT s.group_id, st.is_active INTO v_group, v_active
    FROM student s JOIN student_status st ON st.status_id = s.status_id
    WHERE s.student_id = NEW.student_id;

    -- оценку ставят только обучающимся (не в академе, не отчисленным)
    IF (TG_OP = 'INSERT' OR NEW.student_id <> OLD.student_id) AND NOT v_active THEN
        RAISE EXCEPTION 'Студент % сейчас не обучается (академический отпуск или отчислен) — оценку поставить нельзя',
            NEW.student_id;
    END IF;

    -- попытки по этому же этапу (модуль / итог) дисциплины в других действующих ведомостях
    SELECT COUNT(*) > 0, COALESCE(bool_or(g.points IS NOT NULL), FALSE) INTO v_attempts, v_passed
    FROM grade g
    JOIN grade_sheet gs ON gs.sheet_id = g.sheet_id AND gs.status <> 'cancelled'
    WHERE g.student_id = NEW.student_id AND gs.item_id = v_sheet.item_id
      AND gs.stage = v_sheet.stage AND gs.sheet_id <> NEW.sheet_id;

    IF v_sheet.sheet_kind = 'main' THEN
        IF v_group <> v_sheet.group_id THEN
            RAISE EXCEPTION 'Студент % не из группы ведомости %', NEW.student_id, v_sheet.sheet_number;
        END IF;
    ELSE
        -- пересдача / комиссия / индивидуальная: студент из группы или уже сдавал эту дисциплину
        -- (например, переведён с долгом), и дисциплина у него ещё не сдана
        IF v_group <> v_sheet.group_id AND NOT v_attempts THEN
            RAISE EXCEPTION 'Студент % не из группы ведомости % и не сдавал эту дисциплину',
                NEW.student_id, v_sheet.sheet_number;
        END IF;
        IF v_passed THEN
            RAISE EXCEPTION 'Студент % уже аттестован по этому этапу дисциплины — пересдача не нужна',
                NEW.student_id;
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER grade_check
BEFORE INSERT OR UPDATE OR DELETE ON grade
FOR EACH ROW EXECUTE FUNCTION trg_grade_check();

-- --- 3. Посещаемость ------------------------------------------------------
-- студент из группы занятия и сейчас обучается; дата попадает в период
-- действия расписания, совпадает по дню недели и по чётности недели
CREATE OR REPLACE FUNCTION trg_attendance_check() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    r RECORD;
    v_week INT;
BEGIN
    SELECT ss.weekday, ss.week_parity, lower(fn_slot_period(ss)) AS valid_from,
           upper(fn_slot_period(ss)) - 1 AS valid_to, t.start_date AS term_start, ta.group_id
    INTO r
    FROM schedule_slot ss
    JOIN teaching_assignment ta ON ta.assignment_id = ss.assignment_id
    JOIN academic_term t ON t.term_id = ss.term_id
    WHERE ss.slot_id = NEW.slot_id;

    IF NOT EXISTS (SELECT 1 FROM student s JOIN student_status st ON st.status_id = s.status_id
                   WHERE s.student_id = NEW.student_id AND s.group_id = r.group_id AND st.is_active) THEN
        RAISE EXCEPTION 'Студент % не обучается в группе этого занятия', NEW.student_id;
    END IF;
    IF NEW.lesson_date > CURRENT_DATE THEN
        RAISE EXCEPTION 'Нельзя отметить посещаемость на будущую дату %', NEW.lesson_date;
    END IF;
    IF NEW.lesson_date NOT BETWEEN r.valid_from AND r.valid_to THEN
        RAISE EXCEPTION 'Дата % вне периода действия расписания (% — %)', NEW.lesson_date, r.valid_from, r.valid_to;
    END IF;
    IF EXTRACT(ISODOW FROM NEW.lesson_date) <> r.weekday THEN
        RAISE EXCEPTION 'Дата % не совпадает с днём недели занятия', NEW.lesson_date;
    END IF;
    IF EXISTS (SELECT 1 FROM holiday WHERE holiday_date = NEW.lesson_date) THEN
        RAISE EXCEPTION '% — праздничный день, занятий нет', NEW.lesson_date;
    END IF;
    -- номер учебной недели от начала семестра (1 = нечётная)
    v_week := fn_week_number(r.term_start, NEW.lesson_date);
    IF (r.week_parity = 'odd' AND v_week % 2 = 0) OR (r.week_parity = 'even' AND v_week % 2 = 1) THEN
        RAISE EXCEPTION 'Занятие идёт только по % неделям, а % — % неделя',
            CASE r.week_parity WHEN 'odd' THEN 'нечётным' ELSE 'чётным' END, NEW.lesson_date,
            CASE WHEN v_week % 2 = 1 THEN 'нечётная' ELSE 'чётная' END;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER attendance_check BEFORE INSERT OR UPDATE ON attendance
FOR EACH ROW EXECUTE FUNCTION trg_attendance_check();

-- --- 4. Староста -----------------------------------------------------------
-- староста — действующий студент этой же группы
CREATE OR REPLACE FUNCTION trg_headman_check() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.headman_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM student s JOIN student_status st ON st.status_id = s.status_id
        WHERE s.student_id = NEW.headman_id AND s.group_id = NEW.group_id AND st.is_active)
    THEN
        RAISE EXCEPTION 'Старостой группы % может быть только обучающийся студент этой группы', NEW.name;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER headman_check BEFORE INSERT OR UPDATE OF headman_id, group_id ON study_group
FOR EACH ROW EXECUTE FUNCTION trg_headman_check();

-- если старосту перевели, отчислили или отправили в академ — снимаем его с должности
CREATE OR REPLACE FUNCTION trg_headman_release() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.group_id <> OLD.group_id
       OR NOT (SELECT is_active FROM student_status WHERE status_id = NEW.status_id) THEN
        UPDATE study_group SET headman_id = NULL
        WHERE headman_id = NEW.student_id AND (group_id <> NEW.group_id
              OR NOT (SELECT is_active FROM student_status WHERE status_id = NEW.status_id));
        IF FOUND THEN
            RAISE NOTICE 'Студент % снят с должности старосты', NEW.student_id;
        END IF;
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER headman_release AFTER UPDATE OF group_id, status_id ON student
FOR EACH ROW EXECUTE FUNCTION trg_headman_release();

-- --- 5. Сотрудники: подтипы, заведующие, директора ----------------------
-- преподаватель — только на преподавательской должности; сотрудник деканата —
-- только на административной или вспомогательной; одновременно обоими быть нельзя
CREATE OR REPLACE FUNCTION trg_employee_subtype() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE v_cat VARCHAR;
BEGIN
    SELECT p.category INTO v_cat FROM employee e JOIN position p ON p.position_id = e.position_id
    WHERE e.employee_id = NEW.employee_id;

    IF TG_TABLE_NAME = 'teacher' THEN
        IF v_cat <> 'teaching' THEN
            RAISE EXCEPTION 'Сотрудник % занимает непреподавательскую должность', NEW.employee_id;
        END IF;
        IF EXISTS (SELECT 1 FROM dean_office_staff WHERE employee_id = NEW.employee_id) THEN
            RAISE EXCEPTION 'Сотрудник % уже числится в деканате', NEW.employee_id;
        END IF;
    ELSE
        IF v_cat = 'teaching' THEN
            RAISE EXCEPTION 'Сотрудник % занимает преподавательскую должность', NEW.employee_id;
        END IF;
        IF EXISTS (SELECT 1 FROM teacher WHERE employee_id = NEW.employee_id) THEN
            RAISE EXCEPTION 'Сотрудник % уже числится преподавателем', NEW.employee_id;
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER teacher_subtype BEFORE INSERT OR UPDATE ON teacher
FOR EACH ROW EXECUTE FUNCTION trg_employee_subtype();
CREATE TRIGGER staff_subtype BEFORE INSERT OR UPDATE ON dean_office_staff
FOR EACH ROW EXECUTE FUNCTION trg_employee_subtype();

-- смена должности не должна ломать подтип
CREATE OR REPLACE FUNCTION trg_position_change() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE v_cat VARCHAR;
BEGIN
    SELECT category INTO v_cat FROM position WHERE position_id = NEW.position_id;
    IF v_cat <> 'teaching' AND EXISTS (SELECT 1 FROM teacher WHERE employee_id = NEW.employee_id) THEN
        RAISE EXCEPTION 'Сотрудник % — преподаватель; сначала переведите его из преподавателей', NEW.employee_id;
    END IF;
    IF v_cat = 'teaching' AND EXISTS (SELECT 1 FROM dean_office_staff WHERE employee_id = NEW.employee_id) THEN
        RAISE EXCEPTION 'Сотрудник % работает в деканате; преподавательская должность недопустима', NEW.employee_id;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER position_change BEFORE UPDATE OF position_id ON employee
FOR EACH ROW EXECUTE FUNCTION trg_position_change();

-- заведующий кафедрой — преподаватель этой кафедры.
-- Проверка отложенная (в конце транзакции), чтобы можно было создать кафедру
-- и её преподавателей в одной транзакции в любом порядке.
CREATE OR REPLACE FUNCTION trg_department_head() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE v_dept INT; v_head INT;
BEGIN
    IF TG_TABLE_NAME = 'department' THEN
        v_dept := NEW.department_id;
        v_head := NEW.head_id;
    ELSE
        -- преподавателя перевели или удалили: не был ли он заведующим?
        SELECT department_id, head_id INTO v_dept, v_head FROM department WHERE head_id = OLD.employee_id;
    END IF;

    IF v_head IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM teacher WHERE employee_id = v_head AND department_id = v_dept)
    THEN
        RAISE EXCEPTION 'Заведующий кафедрой % должен быть преподавателем этой кафедры',
            (SELECT short_name FROM department WHERE department_id = v_dept);
    END IF;
    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER department_head_check
AFTER INSERT OR UPDATE OF head_id ON department
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION trg_department_head();
CREATE CONSTRAINT TRIGGER department_head_teacher_moved
AFTER UPDATE OF department_id OR DELETE ON teacher
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION trg_department_head();

-- директор института — действующий сотрудник на административной должности
CREATE OR REPLACE FUNCTION trg_institute_director() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.director_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM employee e JOIN position p ON p.position_id = e.position_id
        WHERE e.employee_id = NEW.director_id AND p.category = 'administration'
          AND e.dismissal_date IS NULL)
    THEN
        RAISE EXCEPTION 'Директором института может быть только работающий сотрудник на административной должности';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER institute_director BEFORE INSERT OR UPDATE OF director_id ON institute
FOR EACH ROW EXECUTE FUNCTION trg_institute_director();

-- --- 6. Практики, курсовые, ВКР ------------------------------------------
-- строка плана — из плана группы студента и нужного вида
CREATE OR REPLACE FUNCTION trg_student_item_kind() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_kind VARCHAR;
    v_ok   BOOLEAN;
BEGIN
    SELECT d.kind, (ci.curriculum_id = g.curriculum_id) INTO v_kind, v_ok
    FROM curriculum_item ci
    JOIN discipline d ON d.discipline_id = ci.discipline_id
    JOIN student s ON s.student_id = NEW.student_id
    JOIN study_group g ON g.group_id = s.group_id
    WHERE ci.item_id = NEW.item_id;

    IF NOT v_ok THEN
        RAISE EXCEPTION 'Строка плана % не входит в учебный план студента %', NEW.item_id, NEW.student_id;
    END IF;
    IF TG_TABLE_NAME = 'practice_placement' AND v_kind <> 'practice' THEN
        RAISE EXCEPTION 'На практику можно направить только по строке плана вида «практика»';
    END IF;
    IF TG_TABLE_NAME = 'academic_work' AND v_kind NOT IN ('coursework', 'final_attestation') THEN
        RAISE EXCEPTION 'Тему можно закрепить только за курсовой работой или ВКР';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER placement_item_kind BEFORE INSERT OR UPDATE OF student_id, item_id ON practice_placement
FOR EACH ROW EXECUTE FUNCTION trg_student_item_kind();
CREATE TRIGGER work_item_kind BEFORE INSERT OR UPDATE OF student_id, item_id ON academic_work
FOR EACH ROW EXECUTE FUNCTION trg_student_item_kind();

-- --- 7. Приказы: студент, тип и институт ----------------------------------
-- Кто вправе подписывать приказы и подтверждать исправления по институту:
-- его директор или работающий сотрудник деканата этого института на административной
-- должности (заместитель директора).
CREATE OR REPLACE FUNCTION fn_manages_institute(p_employee_id INT, p_institute_id INT)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM employee e JOIN position p ON p.position_id = e.position_id
        WHERE e.employee_id = p_employee_id AND e.dismissal_date IS NULL AND p.category = 'administration'
          AND (EXISTS (SELECT 1 FROM institute i WHERE i.institute_id = p_institute_id AND i.director_id = e.employee_id)
               OR EXISTS (SELECT 1 FROM dean_office_staff d WHERE d.employee_id = e.employee_id
                          AND d.institute_id = p_institute_id)));
$$;

-- Ссылка на приказ из записи о студенте (отпуск, выход, стипендия, практика, тема ВКР)
-- допустима, только если 1) приказ нужного типа, 2) студент включён в этот приказ,
-- 3) приказ издан институтом, в котором учится студент.
CREATE OR REPLACE FUNCTION fn_check_order_ref(p_order_id INT, p_student_id INT, p_codes TEXT[], p_what TEXT)
RETURNS VOID LANGUAGE plpgsql STABLE AS $$
DECLARE
    r       RECORD;
    v_inst  INT;
BEGIN
    SELECT o.order_number, o.institute_id, ot.code, ot.name, i.short_name AS inst
    INTO r
    FROM academic_order o
    JOIN order_type ot ON ot.order_type_id = o.order_type_id
    JOIN institute  i  ON i.institute_id = o.institute_id
    WHERE o.order_id = p_order_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Приказ % не найден', p_order_id;
    END IF;
    IF NOT r.code = ANY (p_codes) THEN
        RAISE EXCEPTION 'Приказ № % («%») не является приказом нужного типа: для записи «%» нужен «%»',
            r.order_number, r.name, p_what,
            (SELECT string_agg(name, '» или «') FROM order_type WHERE code = ANY (p_codes));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM order_student WHERE order_id = p_order_id AND student_id = p_student_id) THEN
        RAISE EXCEPTION 'Студент % не включён в приказ № % — сначала включите его в приказ', p_student_id, r.order_number;
    END IF;
    SELECT fn_group_institute(group_id) INTO v_inst FROM student WHERE student_id = p_student_id;
    IF v_inst IS DISTINCT FROM r.institute_id THEN
        RAISE EXCEPTION 'Приказ № % издан институтом %, а студент % учится в институте %',
            r.order_number, r.inst, p_student_id, (SELECT short_name FROM institute WHERE institute_id = v_inst);
    END IF;
END $$;

-- Универсальный триггер: аргументы — столбец со ссылкой на приказ, допустимые коды
-- типов через запятую и название записи для сообщения. Проверка идёт, только если
-- ссылка или студент меняются (старые записи остаются валидными).
CREATE OR REPLACE FUNCTION trg_order_ref() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE v_order INT := (to_jsonb(NEW) ->> TG_ARGV[0])::INT;
BEGIN
    IF v_order IS NULL THEN RETURN NEW; END IF;
    IF TG_OP = 'UPDATE' AND NEW.student_id = OLD.student_id
       AND (to_jsonb(OLD) ->> TG_ARGV[0]) IS NOT DISTINCT FROM (to_jsonb(NEW) ->> TG_ARGV[0]) THEN
        RETURN NEW;
    END IF;
    PERFORM fn_check_order_ref(v_order, NEW.student_id, string_to_array(TG_ARGV[1], ','), TG_ARGV[2]);
    RETURN NEW;
END $$;

CREATE TRIGGER scholarship_order BEFORE INSERT OR UPDATE OF order_id, student_id ON scholarship
FOR EACH ROW EXECUTE FUNCTION trg_order_ref('order_id', 'scholarship', 'стипендия');
CREATE TRIGGER placement_order BEFORE INSERT OR UPDATE OF order_id, student_id ON practice_placement
FOR EACH ROW EXECUTE FUNCTION trg_order_ref('order_id', 'practice', 'направление на практику');
CREATE TRIGGER work_order BEFORE INSERT OR UPDATE OF order_id, student_id ON academic_work
FOR EACH ROW EXECUTE FUNCTION trg_order_ref('order_id', 'thesis_topics', 'тема курсовой работы или ВКР');
CREATE TRIGGER leave_order BEFORE INSERT OR UPDATE OF order_id, student_id ON academic_leave
FOR EACH ROW EXECUTE FUNCTION trg_order_ref('order_id', 'leave', 'академический отпуск');
CREATE TRIGGER leave_return_order BEFORE INSERT OR UPDATE OF return_order_id, student_id ON academic_leave
FOR EACH ROW EXECUTE FUNCTION trg_order_ref('return_order_id', 'leave_return', 'выход из академического отпуска');

-- Академический отпуск: периоды одного студента не пересекаются по ФАКТИЧЕСКОМУ периоду.
-- При досрочном выходе остаток планового интервала свободен — в нём можно оформить
-- новый отпуск. Блокировка по студенту исключает гонку двух параллельных транзакций.
CREATE OR REPLACE FUNCTION trg_leave_check() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_return DATE;
    v_other  RECORD;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('deanery.academic_leave'), NEW.student_id);

    IF NEW.return_order_id IS NOT NULL THEN
        SELECT effective_date INTO v_return FROM academic_order WHERE order_id = NEW.return_order_id;
        IF v_return <= NEW.start_date THEN
            RAISE EXCEPTION 'Дата выхода из отпуска % должна быть позже его начала %', v_return, NEW.start_date;
        END IF;
        IF EXISTS (SELECT 1 FROM academic_leave WHERE return_order_id = NEW.return_order_id
                   AND student_id = NEW.student_id AND leave_id <> NEW.leave_id) THEN
            RAISE EXCEPTION 'Этот приказ о выходе уже закрывает другой отпуск студента';
        END IF;
    END IF;

    SELECT x.start_date, fn_leave_actual_end(x) AS actual_end INTO v_other
    FROM academic_leave x
    WHERE x.student_id = NEW.student_id AND x.leave_id <> NEW.leave_id
      AND daterange(x.start_date, fn_leave_actual_end(x), '[]')
       && daterange(NEW.start_date, fn_leave_actual_end(NEW), '[]')
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'Академический отпуск % — % пересекается с другим отпуском студента (фактически % — %)',
            NEW.start_date, fn_leave_actual_end(NEW), v_other.start_date, v_other.actual_end;
    END IF;
    RETURN NEW;
END $$;

-- имя на «v»: срабатывает после проверок приказов (триггеры BEFORE идут по алфавиту)
CREATE TRIGGER leave_valid_period BEFORE INSERT OR UPDATE OF student_id, start_date, end_date, return_order_id ON academic_leave
FOR EACH ROW EXECUTE FUNCTION trg_leave_check();

-- Включение студента в приказ (order_student):
-- • институт приказа = институт группы студента (для восстановления — новой группы),
--   новая группа — того же института и не в архиве;
-- • новая группа обязательна для перевода и восстановления, допустима при выходе
--   из академа (возвращение в группу следующего набора), в остальных приказах — нет;
-- • для нового (ещё не исполненного) приказа, меняющего статус или группу, —
--   студент в подходящем статусе и у него нет другого запланированного приказа;
-- • приказы, которые не меняют статус и группу (стипендия, практика, темы ВКР),
--   считаются исполненными сразу;
-- • исполненную строку менять нельзя (кроме текста основания).
CREATE OR REPLACE FUNCTION fn_order_changes_state(p_code TEXT) RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE AS $$
    SELECT p_code IN ('enroll', 'expel', 'transfer', 'leave', 'leave_return', 'reinstate', 'graduate');
$$;

CREATE OR REPLACE FUNCTION trg_order_student_check() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    r         RECORD;
    v_group   INT;
    v_status  TEXT;
    v_allowed TEXT[];
    v_pending RECORD;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.applied_at IS NOT NULL THEN
            IF (NEW.order_id, NEW.student_id, NEW.new_group_id, NEW.applied_at)
               IS DISTINCT FROM (OLD.order_id, OLD.student_id, OLD.new_group_id, OLD.applied_at) THEN
                RAISE EXCEPTION 'Приказ уже исполнен для студента % — изменить его нельзя, оформите новый приказ', OLD.student_id;
            END IF;
            RETURN NEW;
        END IF;
        -- исполнение запланированного приказа: меняется только applied_at
        IF NEW.applied_at IS NOT NULL AND (NEW.order_id, NEW.student_id, NEW.new_group_id)
                                          IS NOT DISTINCT FROM (OLD.order_id, OLD.student_id, OLD.new_group_id) THEN
            RETURN NEW;
        END IF;
    END IF;

    SELECT o.order_number, o.institute_id, o.effective_date, ot.code, ot.name, i.short_name AS inst
    INTO r
    FROM academic_order o
    JOIN order_type ot ON ot.order_type_id = o.order_type_id
    JOIN institute  i  ON i.institute_id = o.institute_id
    WHERE o.order_id = NEW.order_id;

    SELECT s.group_id, st.name INTO v_group, v_status
    FROM student s JOIN student_status st ON st.status_id = s.status_id
    WHERE s.student_id = NEW.student_id;

    -- новая группа
    IF r.code IN ('transfer', 'reinstate') AND NEW.new_group_id IS NULL THEN
        RAISE EXCEPTION 'В приказе «%» нужно указать новую группу', r.name;
    END IF;
    IF NEW.new_group_id IS NOT NULL AND r.code NOT IN ('transfer', 'reinstate', 'leave_return') THEN
        RAISE EXCEPTION 'В приказе «%» новая группа не указывается', r.name;
    END IF;

    -- институт
    IF r.code <> 'reinstate' AND fn_group_institute(v_group) <> r.institute_id THEN
        RAISE EXCEPTION 'Приказ № % издан институтом %, а студент % учится в институте %',
            r.order_number, r.inst, NEW.student_id,
            (SELECT short_name FROM institute WHERE institute_id = fn_group_institute(v_group));
    END IF;
    IF NEW.new_group_id IS NOT NULL THEN
        IF fn_group_institute(NEW.new_group_id) <> r.institute_id THEN
            RAISE EXCEPTION 'Группа % относится к другому институту, а приказ № % издан институтом %',
                (SELECT name FROM study_group WHERE group_id = NEW.new_group_id), r.order_number, r.inst;
        END IF;
        IF (SELECT is_archived FROM study_group WHERE group_id = NEW.new_group_id) THEN
            RAISE EXCEPTION 'Группа % в архиве — перевести или восстановить в неё нельзя',
                (SELECT name FROM study_group WHERE group_id = NEW.new_group_id);
        END IF;
    END IF;

    IF NOT fn_order_changes_state(r.code) THEN
        IF TG_OP = 'INSERT' THEN
            NEW.applied_at := COALESCE(NEW.applied_at, now());   -- изменений статуса нет — исполнен сразу
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.applied_at IS NOT NULL THEN
        RETURN NEW;          -- историческая запись при загрузке данных (задать applied_at может только владелец БД)
    END IF;

    -- новый приказ, меняющий статус или группу
    SELECT o.order_number, o.effective_date INTO v_pending
    FROM order_student os JOIN academic_order o ON o.order_id = os.order_id
    WHERE os.student_id = NEW.student_id AND os.applied_at IS NULL AND os.order_id <> NEW.order_id;
    IF FOUND THEN
        RAISE EXCEPTION 'У студента % уже есть запланированный приказ № % (вступает в силу %) — дождитесь его исполнения или отмените: CALL sp_cancel_scheduled_order(''%'', %)',
            NEW.student_id, v_pending.order_number, v_pending.effective_date, v_pending.order_number, NEW.student_id;
    END IF;

    v_allowed := CASE r.code
        WHEN 'enroll'       THEN ARRAY['Зачислен']
        WHEN 'expel'        THEN ARRAY['Обучается', 'Академический отпуск']
        WHEN 'leave_return' THEN ARRAY['Академический отпуск']
        WHEN 'reinstate'    THEN ARRAY['Отчислен']
        ELSE ARRAY['Обучается'] END;          -- transfer, leave, graduate
    IF NOT v_status = ANY (v_allowed) THEN
        RAISE EXCEPTION 'Нельзя включить студента % в приказ «%»: студент в статусе «%»', NEW.student_id, r.name, v_status;
    END IF;
    IF r.code = 'transfer' AND NEW.new_group_id = v_group THEN
        RAISE EXCEPTION 'Студент % уже учится в этой группе', NEW.student_id;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER order_student_check BEFORE INSERT OR UPDATE ON order_student
FOR EACH ROW EXECUTE FUNCTION trg_order_student_check();

-- Исполненный приказ из истории не удаляется; запланированный — только через
-- sp_cancel_scheduled_order (она же снимает связанный отпуск)
CREATE OR REPLACE FUNCTION trg_order_student_delete() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.applied_at IS NOT NULL THEN
        RAISE EXCEPTION 'Приказ уже исполнен для студента % — удалить его из приказа нельзя', OLD.student_id;
    END IF;
    RETURN OLD;
END $$;

CREATE TRIGGER order_student_delete BEFORE DELETE ON order_student
FOR EACH ROW EXECUTE FUNCTION trg_order_student_delete();

-- Тип, институт и даты приказа, в который уже включены студенты, менять нельзя:
-- иначе разошлись бы проверки, сделанные при включении, и даты исполнения.
CREATE OR REPLACE FUNCTION trg_order_immutable() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.order_type_id, NEW.institute_id, NEW.order_date, NEW.effective_date)
       IS DISTINCT FROM (OLD.order_type_id, OLD.institute_id, OLD.order_date, OLD.effective_date)
       AND EXISTS (SELECT 1 FROM order_student WHERE order_id = OLD.order_id) THEN
        RAISE EXCEPTION 'В приказ № % уже включены студенты — тип, институт и даты менять нельзя; отмените запланированный приказ и оформите новый',
            OLD.order_number;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER order_immutable BEFORE UPDATE OF order_type_id, institute_id, order_date, effective_date ON academic_order
FOR EACH ROW EXECUTE FUNCTION trg_order_immutable();

-- --- 8. Заявки: дата выполнения, когда статус стал финальным ---------------
CREATE OR REPLACE FUNCTION trg_request_complete() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT is_final FROM request_status WHERE request_status_id = NEW.request_status_id)
       AND NEW.completed_at IS NULL THEN
        NEW.completed_at := now();
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER request_complete
BEFORE INSERT OR UPDATE OF request_status_id ON document_request
FOR EACH ROW EXECUTE FUNCTION trg_request_complete();

-- --- 9. Шифр группы по правилам МГТУ «СТАНКИН» ----------------------------
-- Ожидаемое начало шифра для учебного плана: буква института, формы обучения,
-- уровня образования и год набора, например «ИДБ-24-». Если буква в справочнике
-- не задана, на её месте «?» и эта позиция не проверяется.
CREATE OR REPLACE FUNCTION fn_group_prefix(p_curriculum_id INT) RETURNS TEXT
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(i.group_letter, '?') || COALESCE(sf.group_letter, '?') || COALESCE(el.group_letter, '?')
           || '-' || lpad((c.start_year % 100)::TEXT, 2, '0') || '-'
    FROM curriculum c
    JOIN study_form      sf ON sf.study_form_id = c.study_form_id
    JOIN study_program   sp ON sp.program_id = c.program_id
    JOIN specialty       s  ON s.specialty_id = sp.specialty_id
    JOIN education_level el ON el.level_id = s.level_id
    JOIN department      d  ON d.department_id = sp.department_id
    JOIN institute       i  ON i.institute_id = d.institute_id
    WHERE c.curriculum_id = p_curriculum_id;
$$;

CREATE OR REPLACE FUNCTION trg_group_name() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_prefix TEXT := fn_group_prefix(NEW.curriculum_id);
    i INT;
BEGIN
    FOR i IN 1 .. length(v_prefix) LOOP
        IF substr(v_prefix, i, 1) <> '?' AND substr(v_prefix, i, 1) <> substr(NEW.name, i, 1) THEN
            RAISE EXCEPTION 'Шифр группы «%» не соответствует учебному плану: ожидается «%NN», где NN — номер группы (И — институт ИИТ, Д — дневная форма, Б/М — бакалавриат/магистратура, далее год набора)',
                NEW.name, v_prefix;
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

CREATE TRIGGER group_name_check BEFORE INSERT OR UPDATE OF name, curriculum_id ON study_group
FOR EACH ROW EXECUTE FUNCTION trg_group_name();

-- =====================================================================
-- КОНФЛИКТЫ РАСПИСАНИЯ
-- Два занятия пересекаются, если совпадают день и пара, пересекаются
-- периоды действия и чётность недели (every пересекается с odd и even).
-- Тогда запрещено:
--   • одна аудитория — кроме лекционного потока;
--   • один преподаватель — кроме лекционного потока;
--   • одна группа — кроме занятий разных подгрупп.
-- Поток: один преподаватель читает одну лекцию нескольким группам в одной аудитории.
-- Дополнительно: суммарная численность групп не должна превышать вместимость аудитории.
-- =====================================================================
CREATE OR REPLACE FUNCTION fn_check_slot(p_slot_id INT) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    c RECORD;
    v_people INT;
    v_capacity INT;
BEGIN
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
END $$;

CREATE OR REPLACE FUNCTION trg_schedule_conflicts() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE s RECORD;
BEGIN
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
END $$;

CREATE TRIGGER schedule_conflicts AFTER INSERT OR UPDATE ON schedule_slot
FOR EACH ROW EXECUTE FUNCTION trg_schedule_conflicts();
CREATE TRIGGER assignment_schedule_conflicts
AFTER UPDATE OF teacher_id, group_id, subgroup, item_id, lesson_type_id ON teaching_assignment
FOR EACH ROW EXECUTE FUNCTION trg_schedule_conflicts();

-- =====================================================================
-- АУДИТ ИЗМЕНЕНИЙ
-- Кто изменил: роль PostgreSQL + пользователь системы и запрос агента из
-- параметров сессии. Приложение/агент перед изменениями выполняет:
--   SET LOCAL app.user_id = '2';  SET LOCAL app.agent_request_id = '15';
-- Функция SECURITY DEFINER: пишет в журнал, даже если у роли нет прав на него.
-- =====================================================================
CREATE OR REPLACE FUNCTION trg_audit() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
DECLARE
    v_old  JSONB := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END;
    v_new  JSONB := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END;
    v_row  JSONB := COALESCE(to_jsonb(NEW), to_jsonb(OLD));
    v_key  TEXT;
    v_id   BIGINT;
    v_hidden CONSTANT TEXT[] := ARRAY['password_hash', 'passport_series', 'passport_number', 'snils', 'inn'];
BEGIN
    IF TG_OP = 'DELETE' THEN v_row := v_old; END IF;

    -- ничего не изменилось — не пишем
    IF TG_OP = 'UPDATE' AND v_old = v_new THEN RETURN NULL; END IF;

    SELECT string_agg(a.attname || '=' || COALESCE(v_row ->> a.attname, 'NULL'), ', ' ORDER BY k.ord)
    INTO v_key
    FROM pg_index i
    CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
    WHERE i.indrelid = TG_RELID AND i.indisprimary;

    INSERT INTO audit_log (table_name, record_key, operation, db_user, app_user_id, agent_request_id)
    VALUES (TG_TABLE_NAME, v_key, left(TG_OP, 1), session_user,
            NULLIF(current_setting('app.user_id', TRUE), '')::INT,
            NULLIF(current_setting('app.agent_request_id', TRUE), '')::INT)
    RETURNING audit_id INTO v_id;

    INSERT INTO audit_log_detail (audit_id, column_name, old_value, new_value)
    SELECT v_id, x.col,
           CASE WHEN x.col = ANY (v_hidden) AND x.old_v IS NOT NULL THEN '***' ELSE x.old_v END,
           CASE WHEN x.col = ANY (v_hidden) AND x.new_v IS NOT NULL THEN '***' ELSE x.new_v END
    FROM (SELECT COALESCE(o.key, n.key) AS col, o.value AS old_v, n.value AS new_v
          FROM jsonb_each_text(COALESCE(v_old, '{}')) o
          FULL JOIN jsonb_each_text(COALESCE(v_new, '{}')) n ON n.key = o.key) x
    WHERE x.old_v IS DISTINCT FROM x.new_v;

    RETURN NULL;
END $$;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['person', 'student', 'contact_person', 'student_contact', 'organization_contact', 'employee', 'teacher', 'dean_office_staff',
        'study_group', 'curriculum', 'curriculum_item', 'teaching_assignment', 'schedule_slot', 'attendance',
        'grade_sheet', 'grade', 'academic_order', 'order_student', 'scholarship', 'academic_leave',
        'practice_placement', 'academic_work', 'document_request', 'app_user']
    LOOP
        EXECUTE format('CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON %I
                        FOR EACH ROW EXECUTE FUNCTION trg_audit()', t);
    END LOOP;
END $$;

-- журнал нельзя исправить задним числом
CREATE OR REPLACE FUNCTION trg_audit_readonly() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Журнал изменений доступен только для чтения';
END $$;

CREATE TRIGGER audit_log_readonly BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION trg_audit_readonly();
CREATE TRIGGER audit_detail_readonly BEFORE UPDATE OR DELETE ON audit_log_detail
FOR EACH ROW EXECUTE FUNCTION trg_audit_readonly();

-- =====================================================================
-- ПРОЦЕДУРЫ ТИПОВЫХ ОПЕРАЦИЙ ДЕКАНАТА (удобно вызывать агенту)
-- =====================================================================

-- Институт, к которому относится группа (через программу и выпускающую кафедру)
CREATE OR REPLACE FUNCTION fn_group_institute(p_group_id INT) RETURNS INT
LANGUAGE sql STABLE AS $$
    SELECT d.institute_id
    FROM study_group g
    JOIN curriculum    c ON c.curriculum_id = g.curriculum_id
    JOIN study_program s ON s.program_id = c.program_id
    JOIN department    d ON d.department_id = s.department_id
    WHERE g.group_id = p_group_id;
$$;

-- Проверка текущего статуса студента перед операцией
CREATE OR REPLACE FUNCTION fn_require_status(p_student_id INT, p_allowed TEXT[], p_action TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE v_status TEXT;
BEGIN
    SELECT st.name INTO v_status FROM student s JOIN student_status st ON st.status_id = s.status_id
    WHERE s.student_id = p_student_id;
    IF v_status IS NULL THEN
        RAISE EXCEPTION 'Студент % не найден', p_student_id;
    END IF;
    IF NOT v_status = ANY (p_allowed) THEN
        RAISE EXCEPTION 'Нельзя выполнить «%»: студент % в статусе «%»', p_action, p_student_id, v_status;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- ПРИКАЗЫ ВСТУПАЮТ В СИЛУ В СВОЮ ДАТУ
-- Процедура записывает приказ сразу, а статус, группу и стипендии меняет
-- только в день вступления в силу. Наступившие приказы исполняет
-- fn_apply_due_orders(): процедуры вызывают её сами, а для приказов на будущее
-- её нужно запускать раз в сутки (планировщик бэкенда или pg_cron):
--     SELECT deanery.fn_apply_due_orders();
-- Пока приказ не исполнен, он виден в v_pending_orders.
-- ---------------------------------------------------------------------

-- Исполнить один приказ для одного студента (внутренняя функция)
CREATE OR REPLACE FUNCTION fn_apply_order(p_order_id INT, p_student_id INT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
DECLARE
    r       RECORD;
    v_due   DATE;
    v_leave academic_leave%ROWTYPE;
BEGIN
    SELECT os.applied_at, os.new_group_id, o.order_number, ot.code, ot.name, st.name AS status
    INTO r
    FROM order_student os
    JOIN academic_order o  ON o.order_id = os.order_id
    JOIN order_type     ot ON ot.order_type_id = o.order_type_id
    JOIN student        s  ON s.student_id = os.student_id
    JOIN student_status st ON st.status_id = s.status_id
    WHERE os.order_id = p_order_id AND os.student_id = p_student_id
    FOR UPDATE OF os;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Студент % не включён в приказ %', p_student_id, p_order_id;
    END IF;
    IF r.applied_at IS NOT NULL THEN RETURN; END IF;          -- уже исполнен

    v_due := fn_order_due_date(p_order_id, p_student_id);
    IF v_due > CURRENT_DATE THEN
        RAISE EXCEPTION 'Приказ № % вступает в силу % — исполнять его раньше нельзя', r.order_number, v_due;
    END IF;

    CASE r.code
    WHEN 'enroll' THEN
        UPDATE student SET status_id = (SELECT status_id FROM student_status WHERE name = 'Обучается')
        WHERE student_id = p_student_id;
    WHEN 'expel' THEN
        UPDATE student SET status_id = (SELECT status_id FROM student_status WHERE name = 'Отчислен')
        WHERE student_id = p_student_id;
        -- стипендия выплачивается по день отчисления
        UPDATE scholarship SET end_date = GREATEST(v_due, start_date + 1)
        WHERE student_id = p_student_id AND end_date > v_due;
    WHEN 'transfer' THEN
        UPDATE student SET group_id = r.new_group_id WHERE student_id = p_student_id;
    WHEN 'leave' THEN
        SELECT * INTO v_leave FROM academic_leave WHERE order_id = p_order_id AND student_id = p_student_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'К приказу № % нет записи об академическом отпуске студента %', r.order_number, p_student_id;
        END IF;
        UPDATE student SET status_id = (SELECT status_id FROM student_status WHERE name = 'Академический отпуск')
        WHERE student_id = p_student_id;
        -- на время академа стипендия не выплачивается
        UPDATE scholarship SET end_date = GREATEST(v_leave.start_date, start_date + 1)
        WHERE student_id = p_student_id AND end_date > v_leave.start_date;
    WHEN 'leave_return' THEN
        -- если приказ о выходе ещё не привязан к отпуску — привязываем к текущему
        IF NOT EXISTS (SELECT 1 FROM academic_leave WHERE return_order_id = p_order_id AND student_id = p_student_id) THEN
            UPDATE academic_leave SET return_order_id = p_order_id
            WHERE leave_id = (SELECT leave_id FROM academic_leave
                              WHERE student_id = p_student_id AND return_order_id IS NULL AND start_date <= CURRENT_DATE
                              ORDER BY start_date DESC LIMIT 1);
            IF NOT FOUND THEN
                RAISE EXCEPTION 'У студента % нет академического отпуска, из которого можно выйти по приказу № %',
                    p_student_id, r.order_number;
            END IF;
        END IF;
        UPDATE student SET status_id = (SELECT status_id FROM student_status WHERE name = 'Обучается'),
                           group_id = COALESCE(r.new_group_id, group_id)
        WHERE student_id = p_student_id;
    WHEN 'reinstate' THEN
        UPDATE student SET status_id = (SELECT status_id FROM student_status WHERE name = 'Обучается'),
                           group_id = r.new_group_id
        WHERE student_id = p_student_id;
    WHEN 'graduate' THEN
        UPDATE student SET status_id = (SELECT status_id FROM student_status WHERE name = 'Выпускник')
        WHERE student_id = p_student_id;
    ELSE
        NULL;                 -- стипендия, практика, темы ВКР: статус и группа не меняются
    END CASE;

    UPDATE order_student SET applied_at = now() WHERE order_id = p_order_id AND student_id = p_student_id;
END $$;

-- Исполнить все наступившие приказы (по одному студенту или по всем).
-- Для всех: ошибка в одном приказе не останавливает остальные — она выводится
-- предупреждением, а приказ остаётся в v_pending_orders. Возвращает число исполненных.
CREATE OR REPLACE FUNCTION fn_apply_due_orders(p_student_id INT DEFAULT NULL)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
DECLARE
    r RECORD;
    n INT := 0;
BEGIN
    FOR r IN
        SELECT os.order_id, os.student_id, o.order_number, fn_order_due_date(os.order_id, os.student_id) AS due
        FROM order_student os JOIN academic_order o ON o.order_id = os.order_id
        WHERE os.applied_at IS NULL
          AND (p_student_id IS NULL OR os.student_id = p_student_id)
          AND fn_order_due_date(os.order_id, os.student_id) <= CURRENT_DATE
        ORDER BY 4, os.order_id
    LOOP
        IF p_student_id IS NOT NULL THEN
            PERFORM fn_apply_order(r.order_id, r.student_id);
            n := n + 1;
        ELSE
            BEGIN
                PERFORM fn_apply_order(r.order_id, r.student_id);
                n := n + 1;
            EXCEPTION WHEN OTHERS THEN
                RAISE WARNING 'Приказ № % для студента % не исполнен: %', r.order_number, r.student_id, SQLERRM;
            END;
        END IF;
    END LOOP;
    RETURN n;
END $$;

-- Общая подготовка операции над студентом: исполнить наступившие приказы,
-- проверить дату вступления в силу, отсутствие других запланированных приказов и статус
CREATE OR REPLACE FUNCTION fn_prepare_operation(p_student_id INT, p_allowed TEXT[], p_action TEXT, p_effective DATE)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE v_pending RECORD;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM student WHERE student_id = p_student_id) THEN
        RAISE EXCEPTION 'Студент % не найден', p_student_id;
    END IF;
    PERFORM fn_apply_due_orders(p_student_id);
    IF p_effective IS NULL OR p_effective < CURRENT_DATE THEN
        RAISE EXCEPTION 'Дата вступления в силу % уже прошла — приказ задним числом не оформляется', p_effective;
    END IF;
    SELECT o.order_number, ot.name, fn_order_due_date(o.order_id, os.student_id) AS due INTO v_pending
    FROM order_student os JOIN academic_order o ON o.order_id = os.order_id
    JOIN order_type ot ON ot.order_type_id = o.order_type_id
    WHERE os.student_id = p_student_id AND os.applied_at IS NULL;
    IF FOUND THEN
        RAISE EXCEPTION 'Нельзя выполнить «%»: у студента % уже есть запланированный приказ № % («%», вступает в силу %) — дождитесь его исполнения или отмените: CALL sp_cancel_scheduled_order(''%'', %)',
            p_action, p_student_id, v_pending.order_number, v_pending.name, v_pending.due, v_pending.order_number, p_student_id;
    END IF;
    PERFORM fn_require_status(p_student_id, p_allowed, p_action);
END $$;

-- Создать приказ и включить в него студента; возвращает id приказа.
-- p_date — дата подписания, p_effective — дата вступления в силу (по умолчанию = p_date).
CREATE OR REPLACE FUNCTION fn_student_order(
    p_student_id INT, p_type_code TEXT, p_order_number TEXT, p_title TEXT,
    p_signed_by INT, p_reason TEXT, p_new_group INT DEFAULT NULL, p_date DATE DEFAULT CURRENT_DATE,
    p_effective DATE DEFAULT NULL
) RETURNS INT LANGUAGE plpgsql AS $$
DECLARE v_order INT; v_group INT;
BEGIN
    SELECT group_id INTO v_group FROM student WHERE student_id = p_student_id;
    IF v_group IS NULL THEN RAISE EXCEPTION 'Студент % не найден', p_student_id; END IF;
    IF NOT EXISTS (SELECT 1 FROM order_type WHERE code = p_type_code) THEN
        RAISE EXCEPTION 'Тип приказа «%» не найден; допустимо: %', p_type_code,
            (SELECT string_agg(code, ', ' ORDER BY order_type_id) FROM order_type);
    END IF;

    -- приказ о восстановлении издаёт институт, куда восстанавливают
    INSERT INTO academic_order (order_number, order_date, order_type_id, institute_id,
                                signed_by_id, title, effective_date)
    VALUES (p_order_number, p_date,
            (SELECT order_type_id FROM order_type WHERE code = p_type_code),
            fn_group_institute(CASE WHEN p_type_code = 'reinstate' THEN COALESCE(p_new_group, v_group) ELSE v_group END),
            p_signed_by, p_title, COALESCE(p_effective, p_date))
    RETURNING order_id INTO v_order;

    INSERT INTO order_student (order_id, student_id, new_group_id, reason)
    VALUES (v_order, p_student_id, p_new_group, p_reason);
    RETURN v_order;
END $$;

-- Зачисление: персона + студент + строка приказа, одной транзакцией.
-- p_effective_date — дата начала обучения (по умолчанию = дате приказа). Если она
-- в будущем, студент получает статус «Зачислен» и станет «Обучается» в этот день.
CREATE OR REPLACE FUNCTION fn_enroll_student(
    p_last_name TEXT, p_first_name TEXT, p_middle_name TEXT,
    p_gender CHAR, p_birth_date DATE, p_email TEXT, p_phone TEXT,
    p_group_name TEXT, p_funding TEXT, p_record_book TEXT,
    p_order_number TEXT, p_order_date DATE, p_signed_by INT,
    p_effective_date DATE DEFAULT NULL
) RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
DECLARE
    v_person INT; v_student INT; v_order INT; v_group INT; v_eff DATE;
BEGIN
    SELECT group_id INTO v_group FROM study_group WHERE name = p_group_name;
    IF v_group IS NULL THEN RAISE EXCEPTION 'Группа % не найдена', p_group_name; END IF;
    IF NOT EXISTS (SELECT 1 FROM funding_type WHERE name = p_funding) THEN
        RAISE EXCEPTION 'Основа обучения «%» не найдена; допустимо: %', p_funding,
            (SELECT string_agg(name, ', ') FROM funding_type);
    END IF;

    -- один приказ о зачислении может включать много студентов
    SELECT order_id, effective_date INTO v_order, v_eff FROM academic_order WHERE order_number = p_order_number;
    IF v_order IS NOT NULL AND p_effective_date IS NOT NULL AND p_effective_date <> v_eff THEN
        RAISE EXCEPTION 'Приказ № % уже существует и вступает в силу %, а не %', p_order_number, v_eff, p_effective_date;
    END IF;
    v_eff := COALESCE(v_eff, p_effective_date, p_order_date);

    INSERT INTO person (last_name, first_name, middle_name, gender, birth_date, email, phone)
    VALUES (p_last_name, p_first_name, p_middle_name, p_gender, p_birth_date, p_email, p_phone)
    RETURNING person_id INTO v_person;

    INSERT INTO student (person_id, record_book_number, group_id, funding_type_id,
                         status_id, enrollment_date)
    VALUES (v_person, p_record_book, v_group,
            (SELECT funding_type_id FROM funding_type WHERE name = p_funding),
            (SELECT status_id FROM student_status WHERE name = 'Зачислен'),
            v_eff)
    RETURNING student_id INTO v_student;

    IF v_order IS NULL THEN
        INSERT INTO academic_order (order_number, order_date, order_type_id, institute_id,
                                    signed_by_id, title, effective_date)
        VALUES (p_order_number, p_order_date,
                (SELECT order_type_id FROM order_type WHERE code = 'enroll'),
                fn_group_institute(v_group), p_signed_by, 'О зачислении', v_eff)
        RETURNING order_id INTO v_order;
    END IF;

    INSERT INTO order_student (order_id, student_id, reason)
    VALUES (v_order, v_student, 'Зачисление');
    PERFORM fn_apply_due_orders(v_student);          -- если дата начала обучения уже наступила
    RETURN v_student;
END $$;

-- Отчисление: приказ; в день вступления в силу — статус «Отчислен» и прекращение стипендий
CREATE OR REPLACE PROCEDURE sp_expel_student(
    p_student_id INT, p_order_number TEXT, p_reason TEXT, p_signed_by INT,
    p_effective DATE DEFAULT CURRENT_DATE
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
BEGIN
    PERFORM fn_prepare_operation(p_student_id, ARRAY['Обучается', 'Академический отпуск'], 'отчисление', p_effective);
    PERFORM fn_student_order(p_student_id, 'expel', p_order_number, 'Об отчислении', p_signed_by, p_reason,
                             p_effective => p_effective);
    PERFORM fn_apply_due_orders(p_student_id);
END $$;

-- Перевод в другую группу того же института; в день вступления в силу — смена группы
CREATE OR REPLACE PROCEDURE sp_transfer_student(
    p_student_id INT, p_new_group TEXT, p_order_number TEXT, p_signed_by INT,
    p_effective DATE DEFAULT CURRENT_DATE
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
DECLARE v_group INT;
BEGIN
    PERFORM fn_prepare_operation(p_student_id, ARRAY['Обучается'], 'перевод', p_effective);
    SELECT group_id INTO v_group FROM study_group WHERE name = p_new_group;
    IF v_group IS NULL THEN RAISE EXCEPTION 'Группа % не найдена', p_new_group; END IF;
    IF v_group = (SELECT group_id FROM student WHERE student_id = p_student_id) THEN
        RAISE EXCEPTION 'Студент % уже учится в группе %', p_student_id, p_new_group;
    END IF;

    PERFORM fn_student_order(p_student_id, 'transfer', p_order_number, 'О переводе',
                             p_signed_by, 'Личное заявление', v_group, p_effective => p_effective);
    PERFORM fn_apply_due_orders(p_student_id);
END $$;

-- Академический отпуск: приказ + период. Статус «Академический отпуск» и остановка
-- стипендии — с даты начала отпуска (если она в будущем — в тот день).
-- Начало может быть и в прошлом (отпуск по заявлению задним числом): тогда приказ
-- вступает в силу сегодня, а период начинается с указанной даты.
CREATE OR REPLACE PROCEDURE sp_grant_academic_leave(
    p_student_id INT, p_reason TEXT, p_start DATE, p_end DATE,
    p_order_number TEXT, p_signed_by INT
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
DECLARE v_order INT; v_reason INT; v_eff DATE := GREATEST(p_start, CURRENT_DATE);
BEGIN
    PERFORM fn_prepare_operation(p_student_id, ARRAY['Обучается'], 'академический отпуск', v_eff);
    SELECT reason_id INTO v_reason FROM leave_reason WHERE name = p_reason;
    IF v_reason IS NULL THEN RAISE EXCEPTION 'Причина «%» не найдена в справочнике', p_reason; END IF;

    -- причина хранится в academic_leave; в приказе — только основание
    v_order := fn_student_order(p_student_id, 'leave', p_order_number,
                                'О предоставлении академического отпуска', p_signed_by, 'Личное заявление',
                                p_effective => v_eff);
    INSERT INTO academic_leave (student_id, reason_id, order_id, start_date, end_date)
    VALUES (p_student_id, v_reason, v_order, p_start, p_end);
    PERFORM fn_apply_due_orders(p_student_id);
END $$;

-- Выход из академического отпуска (в том числе досрочный и запланированный).
-- p_return_date — первый учебный день после отпуска. Если он раньше планового
-- окончания, отпуск фактически заканчивается накануне и остаток периода свободен.
-- p_new_group — вернуться в другую группу (обычно — следующего набора).
CREATE OR REPLACE PROCEDURE sp_return_from_leave(
    p_student_id INT, p_order_number TEXT, p_signed_by INT,
    p_return_date DATE DEFAULT CURRENT_DATE, p_new_group TEXT DEFAULT NULL
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
DECLARE v_leave academic_leave%ROWTYPE; v_order INT; v_group INT;
BEGIN
    PERFORM fn_prepare_operation(p_student_id, ARRAY['Академический отпуск'], 'выход из академического отпуска', p_return_date);
    SELECT * INTO v_leave FROM academic_leave
    WHERE student_id = p_student_id AND return_order_id IS NULL
    ORDER BY start_date DESC LIMIT 1;
    IF NOT FOUND THEN RAISE EXCEPTION 'У студента % нет незавершённого академического отпуска', p_student_id; END IF;
    IF p_new_group IS NOT NULL THEN
        SELECT group_id INTO v_group FROM study_group WHERE name = p_new_group;
        IF v_group IS NULL THEN RAISE EXCEPTION 'Группа % не найдена', p_new_group; END IF;
    END IF;

    v_order := fn_student_order(p_student_id, 'leave_return', p_order_number,
                                'О выходе из академического отпуска', p_signed_by, 'Личное заявление',
                                v_group, p_effective => p_return_date);
    UPDATE academic_leave SET return_order_id = v_order WHERE leave_id = v_leave.leave_id;
    IF p_return_date <= v_leave.end_date THEN
        RAISE NOTICE 'Досрочный выход: отпуск фактически заканчивается %, период % — % свободен',
            p_return_date - 1, p_return_date, v_leave.end_date;
    END IF;
    PERFORM fn_apply_due_orders(p_student_id);
END $$;

-- Восстановление отчисленного студента в группу
CREATE OR REPLACE PROCEDURE sp_reinstate_student(
    p_student_id INT, p_group_name TEXT, p_order_number TEXT, p_signed_by INT,
    p_effective DATE DEFAULT CURRENT_DATE
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
DECLARE v_group INT;
BEGIN
    PERFORM fn_prepare_operation(p_student_id, ARRAY['Отчислен'], 'восстановление', p_effective);
    SELECT group_id INTO v_group FROM study_group WHERE name = p_group_name;
    IF v_group IS NULL THEN RAISE EXCEPTION 'Группа % не найдена', p_group_name; END IF;

    PERFORM fn_student_order(p_student_id, 'reinstate', p_order_number, 'О восстановлении',
                             p_signed_by, 'Личное заявление', v_group, p_effective => p_effective);
    PERFORM fn_apply_due_orders(p_student_id);
END $$;

-- Отменить запланированный (ещё не исполненный) приказ для студента.
-- Для приказа об отпуске удаляется и запись об отпуске; для приказа о выходе —
-- отпуск снова длится до планового окончания. Пустой приказ удаляется целиком.
CREATE OR REPLACE PROCEDURE sp_cancel_scheduled_order(p_order_number TEXT, p_student_id INT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
DECLARE v_order INT; v_code TEXT;
BEGIN
    SELECT o.order_id, ot.code INTO v_order, v_code
    FROM academic_order o JOIN order_type ot ON ot.order_type_id = o.order_type_id
    JOIN order_student os ON os.order_id = o.order_id
    WHERE o.order_number = p_order_number AND os.student_id = p_student_id AND os.applied_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Запланированного приказа № % для студента % нет (он уже исполнен или не существует)',
            p_order_number, p_student_id;
    END IF;

    IF v_code = 'leave' THEN
        DELETE FROM academic_leave WHERE order_id = v_order AND student_id = p_student_id;
    ELSIF v_code = 'leave_return' THEN
        UPDATE academic_leave SET return_order_id = NULL WHERE return_order_id = v_order AND student_id = p_student_id;
    ELSIF v_code = 'enroll' THEN
        RAISE EXCEPTION 'Зачисление отменяется удалением студента, а не приказа';
    END IF;
    DELETE FROM order_student WHERE order_id = v_order AND student_id = p_student_id;
    IF NOT EXISTS (SELECT 1 FROM order_student WHERE order_id = v_order) THEN
        DELETE FROM academic_order WHERE order_id = v_order;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- ИСПРАВЛЕНИЕ ОЦЕНКИ В ЗАКРЫТОЙ ВЕДОМОСТИ
--   1) заявка: fn_request_grade_correction (преподаватель-экзаменатор, деканат, агент);
--   2) решение: sp_decide_grade_correction — директор или заместитель директора
--      института группы, не автор заявки; при одобрении оценка меняется в той же
--      транзакции. Обе операции и само изменение оценки пишутся в журнал аудита.
-- Пользователь берётся из параметра сессии app.user_id (его ставит приложение).
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_current_app_user() RETURNS INT
LANGUAGE plpgsql STABLE AS $$
DECLARE v_user INT := NULLIF(current_setting('app.user_id', TRUE), '')::INT;
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'Не указан пользователь системы: перед операцией выполните SET app.user_id = ''<id>''';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app_user WHERE user_id = v_user AND is_active) THEN
        RAISE EXCEPTION 'Пользователь % не найден или отключён', v_user;
    END IF;
    RETURN v_user;
END $$;

CREATE OR REPLACE FUNCTION fn_request_grade_correction(
    p_sheet_number TEXT, p_record_book TEXT, p_new_points INT, p_new_is_absent BOOLEAN, p_reason TEXT
) RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
DECLARE v_grade grade%ROWTYPE; v_id INT;
BEGIN
    SELECT g.* INTO v_grade FROM grade g
    JOIN grade_sheet gs ON gs.sheet_id = g.sheet_id
    JOIN student s ON s.student_id = g.student_id
    WHERE gs.sheet_number = p_sheet_number AND s.record_book_number = p_record_book;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'В ведомости % нет оценки студента с зачёткой %', p_sheet_number, p_record_book;
    END IF;

    INSERT INTO grade_correction (grade_id, old_points, old_is_absent, new_points, new_is_absent, reason, requested_by_id)
    VALUES (v_grade.grade_id, v_grade.points, v_grade.is_absent, p_new_points, COALESCE(p_new_is_absent, FALSE),
            p_reason, fn_current_app_user())
    RETURNING correction_id INTO v_id;
    RETURN v_id;
END $$;

CREATE OR REPLACE PROCEDURE sp_decide_grade_correction(p_correction_id INT, p_approve BOOLEAN, p_comment TEXT DEFAULT NULL)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
DECLARE v_corr grade_correction%ROWTYPE; v_user INT := fn_current_app_user();
BEGIN
    SELECT * INTO v_corr FROM grade_correction WHERE correction_id = p_correction_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Заявка на исправление % не найдена', p_correction_id; END IF;

    IF NOT p_approve THEN
        UPDATE grade_correction SET status = 'rejected', decided_by_id = v_user, decided_at = now(),
                                    decision_comment = p_comment
        WHERE correction_id = p_correction_id;
        RETURN;
    END IF;

    UPDATE grade_correction SET status = 'approved', decided_by_id = v_user, decided_at = now(),
                                decision_comment = p_comment
    WHERE correction_id = p_correction_id;
    UPDATE grade SET points = v_corr.new_points, is_absent = v_corr.new_is_absent
    WHERE grade_id = v_corr.grade_id;
    UPDATE grade_correction SET status = 'applied' WHERE correction_id = p_correction_id;
END $$;

-- =====================================================================
-- ЗАЩИТА ОТ НЕВЕРНЫХ ДЕЙСТВИЙ
-- =====================================================================

-- Даты рождения, зачисления и приёма на работу должны быть правдоподобными
CREATE OR REPLACE FUNCTION trg_person_birth() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.birth_date > CURRENT_DATE - INTERVAL '14 years' THEN
        RAISE EXCEPTION 'Дата рождения % в будущем или человеку меньше 14 лет', NEW.birth_date;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER person_birth BEFORE INSERT OR UPDATE OF birth_date ON person
FOR EACH ROW EXECUTE FUNCTION trg_person_birth();

-- SECURITY DEFINER: проверка читает дату рождения, которую роли агента и «только чтение» не видят
CREATE OR REPLACE FUNCTION trg_student_check() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
DECLARE v_birth DATE;
BEGIN
    SELECT birth_date INTO v_birth FROM person WHERE person_id = NEW.person_id;
    IF NEW.enrollment_date < v_birth + INTERVAL '14 years' OR NEW.enrollment_date > CURRENT_DATE + INTERVAL '1 year' THEN
        RAISE EXCEPTION 'Неправдоподобная дата зачисления %', NEW.enrollment_date;
    END IF;
    IF (TG_OP = 'INSERT' OR NEW.group_id <> OLD.group_id)
       AND (SELECT is_archived FROM study_group WHERE group_id = NEW.group_id) THEN
        RAISE EXCEPTION 'Группа % в архиве — зачислить или перевести в неё нельзя',
            (SELECT name FROM study_group WHERE group_id = NEW.group_id);
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER student_check BEFORE INSERT OR UPDATE OF enrollment_date, person_id, group_id ON student
FOR EACH ROW EXECUTE FUNCTION trg_student_check();

-- Приём и увольнение сотрудника
-- SECURITY DEFINER: проверка читает дату рождения, которую роли агента и «только чтение» не видят
CREATE OR REPLACE FUNCTION trg_employee_check() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = deanery, public AS $$
DECLARE v_birth DATE;
BEGIN
    SELECT birth_date INTO v_birth FROM person WHERE person_id = NEW.person_id;
    IF NEW.hire_date < v_birth + INTERVAL '16 years' OR NEW.hire_date > CURRENT_DATE + INTERVAL '1 year' THEN
        RAISE EXCEPTION 'Неправдоподобная дата приёма на работу %', NEW.hire_date;
    END IF;

    -- нельзя уволить, пока человек директор, заведующий или куратор
    IF NEW.dismissal_date IS NOT NULL AND (TG_OP = 'INSERT' OR OLD.dismissal_date IS NULL) THEN
        IF EXISTS (SELECT 1 FROM institute WHERE director_id = NEW.employee_id) THEN
            RAISE EXCEPTION 'Сотрудник % — директор института; сначала назначьте нового директора', NEW.employee_id;
        END IF;
        IF EXISTS (SELECT 1 FROM department WHERE head_id = NEW.employee_id) THEN
            RAISE EXCEPTION 'Сотрудник % — заведующий кафедрой; сначала назначьте нового заведующего', NEW.employee_id;
        END IF;
        IF EXISTS (SELECT 1 FROM study_group WHERE curator_id = NEW.employee_id AND NOT is_archived) THEN
            RAISE EXCEPTION 'Сотрудник % — куратор группы; сначала назначьте нового куратора', NEW.employee_id;
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER employee_check BEFORE INSERT OR UPDATE OF hire_date, dismissal_date, person_id ON employee
FOR EACH ROW EXECUTE FUNCTION trg_employee_check();

-- Назначать можно только работающих преподавателей и сотрудников.
-- Аргументы триггера — имена проверяемых столбцов; проверка идёт, только
-- если значение меняется (старые записи уволенного сотрудника остаются валидными).
CREATE OR REPLACE FUNCTION trg_employee_active() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_col TEXT;
    v_id  INT;
BEGIN
    FOREACH v_col IN ARRAY TG_ARGV LOOP
        v_id := (to_jsonb(NEW) ->> v_col)::INT;
        CONTINUE WHEN v_id IS NULL;
        CONTINUE WHEN TG_OP = 'UPDATE' AND (to_jsonb(OLD) ->> v_col) IS NOT DISTINCT FROM (to_jsonb(NEW) ->> v_col);
        IF EXISTS (SELECT 1 FROM employee WHERE employee_id = v_id AND dismissal_date IS NOT NULL) THEN
            RAISE EXCEPTION 'Сотрудник % уволен — назначить его нельзя (%)', v_id, v_col;
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

CREATE TRIGGER active_teacher BEFORE INSERT OR UPDATE ON teaching_assignment
FOR EACH ROW EXECUTE FUNCTION trg_employee_active('teacher_id');
CREATE TRIGGER active_examiner BEFORE INSERT OR UPDATE ON grade_sheet
FOR EACH ROW EXECUTE FUNCTION trg_employee_active('examiner_id');
CREATE TRIGGER active_supervisor BEFORE INSERT OR UPDATE ON academic_work
FOR EACH ROW EXECUTE FUNCTION trg_employee_active('supervisor_id', 'reviewer_id');
CREATE TRIGGER active_supervisor BEFORE INSERT OR UPDATE ON practice_placement
FOR EACH ROW EXECUTE FUNCTION trg_employee_active('supervisor_id');
CREATE TRIGGER active_curator BEFORE INSERT OR UPDATE ON study_group
FOR EACH ROW EXECUTE FUNCTION trg_employee_active('curator_id');
CREATE TRIGGER active_head BEFORE INSERT OR UPDATE ON department
FOR EACH ROW EXECUTE FUNCTION trg_employee_active('head_id');
CREATE TRIGGER active_processor BEFORE INSERT OR UPDATE ON document_request
FOR EACH ROW EXECUTE FUNCTION trg_employee_active('processed_by_id');

-- Приказ подписывает работающий руководитель ТОГО ЖЕ института:
-- его директор или заместитель директора (сотрудник деканата этого института)
CREATE OR REPLACE FUNCTION trg_order_signer() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.signed_by_id IS NOT NULL AND NOT fn_manages_institute(NEW.signed_by_id, NEW.institute_id) THEN
        RAISE EXCEPTION 'Приказ института % может подписать только работающий руководитель этого института (директор или заместитель)',
            (SELECT short_name FROM institute WHERE institute_id = NEW.institute_id);
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER order_signer BEFORE INSERT OR UPDATE OF signed_by_id, institute_id ON academic_order
FOR EACH ROW EXECUTE FUNCTION trg_order_signer();

-- Жизненный цикл ведомости: открыта → закрыта или отменена; обратно нельзя.
-- Основную ведомость можно закрыть, только когда оценки есть у всех обучающихся
-- студентов группы (неявка — тоже оценка); пересдачу — если в ней есть хоть одна оценка.
CREATE OR REPLACE FUNCTION trg_sheet_status() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE v_missing TEXT;
BEGIN
    IF OLD.status = NEW.status THEN RETURN NEW; END IF;
    IF OLD.status <> 'open' THEN
        RAISE EXCEPTION 'Ведомость % уже %, изменить её статус нельзя — оформите новую ведомость',
            OLD.sheet_number, CASE OLD.status WHEN 'closed' THEN 'закрыта' ELSE 'отменена' END;
    END IF;
    IF NEW.status = 'closed' THEN
        IF NEW.sheet_kind = 'main' THEN
            SELECT string_agg(fn_full_name(p), ', ') INTO v_missing
            FROM student s
            JOIN student_status st ON st.status_id = s.status_id AND st.is_active
            JOIN person p ON p.person_id = s.person_id
            WHERE s.group_id = NEW.group_id
              AND NOT EXISTS (SELECT 1 FROM grade g WHERE g.sheet_id = NEW.sheet_id AND g.student_id = s.student_id);
            IF v_missing IS NOT NULL THEN
                RAISE EXCEPTION 'Нельзя закрыть ведомость %: нет оценок у студентов: %', NEW.sheet_number, v_missing;
            END IF;
        ELSIF NOT EXISTS (SELECT 1 FROM grade WHERE sheet_id = NEW.sheet_id) THEN
            RAISE EXCEPTION 'Нельзя закрыть пустую ведомость %', NEW.sheet_number;
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER sheet_status BEFORE UPDATE OF status ON grade_sheet
FOR EACH ROW EXECUTE FUNCTION trg_sheet_status();

-- Номер семестра не больше срока обучения по плану
CREATE OR REPLACE FUNCTION trg_item_semester() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE v_max INT;
BEGIN
    SELECT CEIL(duration_years * 2) INTO v_max FROM curriculum WHERE curriculum_id = NEW.curriculum_id;
    IF NEW.semester > v_max THEN
        RAISE EXCEPTION 'В этом учебном плане всего % семестров, а указан %', v_max, NEW.semester;
    END IF;
    -- у практики, курсовой работы и ВКР промежуточных модулей нет — только итоговая оценка
    IF NEW.module_count > 0 AND (SELECT kind FROM discipline WHERE discipline_id = NEW.discipline_id) <> 'discipline' THEN
        RAISE EXCEPTION 'У практики, курсовой работы и ВКР модулей нет — module_count должен быть 0';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER item_semester BEFORE INSERT OR UPDATE OF semester, curriculum_id, module_count, discipline_id ON curriculum_item
FOR EACH ROW EXECUTE FUNCTION trg_item_semester();

-- Группу с обучающимися студентами нельзя отправить в архив
CREATE OR REPLACE FUNCTION trg_group_archive() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.is_archived AND NOT OLD.is_archived AND EXISTS (
        SELECT 1 FROM student s JOIN student_status st ON st.status_id = s.status_id
        WHERE s.group_id = NEW.group_id AND st.is_active)
    THEN
        RAISE EXCEPTION 'В группе % есть обучающиеся студенты — архивировать её нельзя', NEW.name;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER group_archive BEFORE UPDATE OF is_archived ON study_group
FOR EACH ROW EXECUTE FUNCTION trg_group_archive();

-- Стипендия: только обучающимся; государственная — только бюджетникам
CREATE OR REPLACE FUNCTION trg_scholarship_check() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE v_active BOOLEAN; v_funding TEXT;
BEGIN
    SELECT st.is_active, ft.name INTO v_active, v_funding
    FROM student s
    JOIN student_status st ON st.status_id = s.status_id
    JOIN funding_type   ft ON ft.funding_type_id = s.funding_type_id
    WHERE s.student_id = NEW.student_id;

    IF TG_OP = 'INSERT' AND NOT v_active THEN
        RAISE EXCEPTION 'Студент % сейчас не обучается — стипендию назначить нельзя', NEW.student_id;
    END IF;
    IF (SELECT budget_only FROM scholarship_type WHERE scholarship_type_id = NEW.scholarship_type_id)
       AND v_funding <> 'Бюджет' THEN
        RAISE EXCEPTION 'Эта стипендия назначается только студентам на бюджете, а студент % учится по договору',
            NEW.student_id;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER scholarship_check BEFORE INSERT OR UPDATE OF student_id, scholarship_type_id ON scholarship
FOR EACH ROW EXECUTE FUNCTION trg_scholarship_check();

-- Выполненную или отклонённую заявку нельзя вернуть в работу
CREATE OR REPLACE FUNCTION trg_request_final() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.request_status_id <> OLD.request_status_id
       AND (SELECT is_final FROM request_status WHERE request_status_id = OLD.request_status_id) THEN
        RAISE EXCEPTION 'Заявка % уже завершена, её статус менять нельзя — создайте новую', OLD.request_id;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER request_final BEFORE UPDATE OF request_status_id ON document_request
FOR EACH ROW EXECUTE FUNCTION trg_request_final();

-- Заявка на исправление оценки в закрытой ведомости.
-- Подача: ведомость закрыта; old_* совпадают с текущей оценкой; новые баллы в шкале;
--   по этому этапу дисциплины у студента нет более поздней попытки (иначе исправлять
--   надо её); автор — работающий пользователь, преподаватель — только экзаменатор ведомости.
-- Решение: pending → approved → applied (одобрение) или pending → rejected;
--   решает директор или заместитель директора института группы, но не автор заявки.
-- Содержание заявки после подачи не меняется.
CREATE OR REPLACE FUNCTION trg_correction_check() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    g        RECORD;
    v_later  TEXT;
    v_emp    INT;
    v_role   TEXT;
BEGIN
    SELECT gr.points, gr.is_absent, gr.student_id, gs.sheet_id, gs.sheet_number, gs.status, gs.item_id,
           gs.stage, gs.exam_date, gs.examiner_id, fn_group_institute(gs.group_id) AS institute_id
    INTO g
    FROM grade gr JOIN grade_sheet gs ON gs.sheet_id = gr.sheet_id
    WHERE gr.grade_id = NEW.grade_id;

    IF TG_OP = 'UPDATE' THEN
        IF (NEW.grade_id, NEW.old_points, NEW.old_is_absent, NEW.new_points, NEW.new_is_absent,
            NEW.reason, NEW.requested_by_id, NEW.requested_at)
           IS DISTINCT FROM (OLD.grade_id, OLD.old_points, OLD.old_is_absent, OLD.new_points, OLD.new_is_absent,
                             OLD.reason, OLD.requested_by_id, OLD.requested_at) THEN
            RAISE EXCEPTION 'Содержание заявки % менять нельзя — подайте новую', OLD.correction_id;
        END IF;
        IF NEW.status = OLD.status THEN
            IF (NEW.decided_by_id, NEW.decided_at, NEW.decision_comment)
               IS DISTINCT FROM (OLD.decided_by_id, OLD.decided_at, OLD.decision_comment) THEN
                RAISE EXCEPTION 'Решение по заявке % уже принято — менять его нельзя', OLD.correction_id;
            END IF;
            RETURN NEW;
        END IF;
        IF NOT ((OLD.status = 'pending' AND NEW.status IN ('approved', 'rejected'))
                OR (OLD.status = 'approved' AND NEW.status = 'applied')) THEN
            RAISE EXCEPTION 'Заявка % уже рассмотрена (%) — повторно принять решение нельзя', OLD.correction_id, OLD.status;
        END IF;
        IF OLD.status = 'approved' THEN
            IF (g.points, g.is_absent) IS DISTINCT FROM (NEW.new_points, NEW.new_is_absent) THEN
                RAISE EXCEPTION 'Заявка % не применена: оценка не равна исправленной', NEW.correction_id;
            END IF;
            RETURN NEW;
        END IF;
    ELSIF NEW.status <> 'pending' THEN
        RETURN NEW;          -- историческая запись при загрузке данных (вставлять заявки напрямую может только владелец БД)
    END IF;

    -- подача и одобрение: оценка всё ещё та, что была при подаче, и её ещё можно исправлять
    IF TG_OP = 'INSERT' OR NEW.status = 'approved' THEN
        IF g.status <> 'closed' THEN
            RAISE EXCEPTION 'Ведомость % %', g.sheet_number,
                CASE g.status WHEN 'open' THEN 'открыта — исправьте оценку обычным изменением, заявка не нужна'
                              ELSE 'отменена — исправлять в ней нечего' END;
        END IF;
        IF (g.points, g.is_absent) IS DISTINCT FROM (NEW.old_points, NEW.old_is_absent) THEN
            RAISE EXCEPTION 'Оценка в ведомости % изменилась после подачи заявки % — заявку нужно подать заново',
                g.sheet_number, NEW.correction_id;
        END IF;
        IF NEW.new_points IS NOT NULL AND fn_mark(NEW.new_points) IS NULL THEN
            RAISE EXCEPTION 'Баллы % вне шкалы: допустимо от % до %', NEW.new_points,
                (SELECT MIN(min_points) FROM score_band), (SELECT MAX(max_points) FROM score_band);
        END IF;
        SELECT gs2.sheet_number INTO v_later
        FROM grade g2 JOIN grade_sheet gs2 ON gs2.sheet_id = g2.sheet_id AND gs2.status <> 'cancelled'
        WHERE g2.student_id = g.student_id AND gs2.item_id = g.item_id AND gs2.stage = g.stage
          AND (COALESCE(gs2.exam_date, DATE 'infinity'), gs2.sheet_id) > (g.exam_date, g.sheet_id)
        ORDER BY gs2.exam_date DESC LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'По этой дисциплине у студента есть более поздняя попытка (ведомость %) — исправлять нужно её',
                v_later;
        END IF;
    END IF;

    IF TG_OP = 'INSERT' THEN
        SELECT e.employee_id, r.code INTO v_emp, v_role
        FROM app_user u JOIN app_role r ON r.role_id = u.role_id
        LEFT JOIN employee e ON e.person_id = u.person_id AND e.dismissal_date IS NULL
        WHERE u.user_id = NEW.requested_by_id AND u.is_active;
        IF NOT FOUND OR v_role = 'student' THEN
            RAISE EXCEPTION 'Подать заявку на исправление оценки может только работающий сотрудник';
        END IF;
        IF v_role = 'teacher' AND v_emp IS DISTINCT FROM g.examiner_id THEN
            RAISE EXCEPTION 'Преподаватель может подать заявку только по своей ведомости (экзаменатор ведомости % — другой)',
                g.sheet_number;
        END IF;
        RETURN NEW;
    END IF;

    -- решение: директор или заместитель директора института группы, не автор
    IF NEW.decided_by_id = NEW.requested_by_id THEN
        RAISE EXCEPTION 'Автор заявки не может сам её подтвердить — нужен другой руководитель';
    END IF;
    SELECT e.employee_id INTO v_emp
    FROM app_user u JOIN employee e ON e.person_id = u.person_id
    WHERE u.user_id = NEW.decided_by_id AND u.is_active;
    IF v_emp IS NULL OR NOT fn_manages_institute(v_emp, g.institute_id) THEN
        RAISE EXCEPTION 'Решение по исправлению оценки принимает директор или заместитель директора института %',
            (SELECT short_name FROM institute WHERE institute_id = g.institute_id);
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER correction_check BEFORE INSERT OR UPDATE ON grade_correction
FOR EACH ROW EXECUTE FUNCTION trg_correction_check();
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON grade_correction
FOR EACH ROW EXECUTE FUNCTION trg_audit();
