-- =====================================================================
--  Примеры CRUD-операций для основных сущностей деканата
--  Скрипт завёрнут в транзакцию с ROLLBACK — его можно запускать
--  сколько угодно раз, данные не изменятся. Для реальных изменений
--  замените ROLLBACK на COMMIT.
--  Записи ищутся по понятным ключам: зачётке, шифру группы, табельному номеру.
-- =====================================================================
SET search_path TO deanery, public;
BEGIN;

-- =====================================================================
-- ОБЗОР: кто есть кто среди студентов
-- =====================================================================
SELECT category, count(*) AS students FROM v_student_summary GROUP BY 1 ORDER BY 2 DESC;
SELECT group_name, course, active_students, budget_students, curator, headman
FROM v_groups ORDER BY group_name;

-- =====================================================================
-- СТУДЕНТЫ
-- =====================================================================

-- CREATE: зачисление одной функцией (персона + студент + приказ)
SELECT fn_enroll_student(
    'Андреев', 'Михаил', 'Юрьевич', 'M', '2008-04-12',
    'andreev.my@stud.univ.ru', '+7 915 200-99-21',
    'ИДБ-26-13', 'Бюджет', '26-1399',
    '901-с/26', CURRENT_DATE, 1) AS new_student_id;

-- READ: карточка, список группы, поиск по фамилии, сводка
SELECT * FROM v_students WHERE record_book_number = '26-1399';
SELECT full_name, record_book_number, funding, status
FROM v_students WHERE group_name = 'ИДБ-23-11' ORDER BY full_name;
SELECT full_name, group_name FROM v_students WHERE full_name ILIKE 'иванов %' ORDER BY group_name;
SELECT * FROM v_student_summary WHERE record_book_number = '23-1101';

-- UPDATE: смена телефона (персональные данные — в person) и основы обучения
UPDATE person SET phone = '+7 916 555-44-33'
WHERE person_id = (SELECT person_id FROM student WHERE record_book_number = '26-1399');

UPDATE student SET funding_type_id = (SELECT funding_type_id FROM funding_type WHERE name = 'Договор')
WHERE record_book_number = '26-1399';

-- UPDATE (бизнес-операции): перевод и отчисление с приказами.
-- Перевод — с сегодняшнего дня (исполняется сразу); отчисление — с даты в будущем:
-- приказ записан, но статус сменится только в день вступления в силу.
-- (подзапрос в аргументах CALL запрещён, поэтому id берём в DO-блоке)
DO $$
DECLARE v_id INT;
BEGIN
    SELECT student_id INTO v_id FROM student WHERE record_book_number = '26-1399';
    CALL sp_transfer_student(v_id, 'ИДБ-26-14', '902-с/26', 1);
    CALL sp_expel_student(v_id, '903-с/26', 'По собственному желанию', 1, CURRENT_DATE + 14);
END $$;
SELECT order_number, order_type, effective_date, state, new_group
FROM v_student_orders WHERE record_book_number = '26-1399' ORDER BY order_date, order_number;
SELECT status, group_name FROM v_students WHERE record_book_number = '26-1399';   -- пока «Обучается»
SELECT student, order_number, order_type, due_date, days_left FROM v_pending_orders ORDER BY due_date;

-- Студент передумал до даты вступления в силу — запланированный приказ отменяется
DO $$
DECLARE v_id INT := (SELECT student_id FROM student WHERE record_book_number = '26-1399');
BEGIN
    CALL sp_cancel_scheduled_order('903-с/26', v_id);
END $$;

-- DELETE: в деканате студентов не удаляют физически (история приказов и оценок),
-- а переводят в статус «Отчислен». Физически можно удалить только ошибочно внесённую
-- запись, по которой ещё ничего не исполнено, — например, зачисление с будущей даты
-- (статус «Зачислен», приказ запланирован):
SELECT fn_enroll_student('Ошибкин', 'Пётр', NULL, 'M', '2008-01-01', NULL, NULL,
                         'ИДБ-26-13', 'Договор', '26-1398', '907-с/26', CURRENT_DATE, 1,
                         p_effective_date => '2027-02-08') AS mistaken_student_id;
SELECT status, enrollment_date FROM v_students WHERE record_book_number = '26-1398';
DELETE FROM order_student WHERE student_id = (SELECT student_id FROM student WHERE record_book_number = '26-1398');
DELETE FROM academic_order WHERE order_number = '907-с/26';
DELETE FROM student WHERE record_book_number = '26-1398';
DELETE FROM person  WHERE last_name = 'Ошибкин' AND first_name = 'Пётр';

-- =====================================================================
-- ПРЕПОДАВАТЕЛИ
-- =====================================================================

-- CREATE: персона → сотрудник → преподаватель
WITH p AS (
    INSERT INTO person (last_name, first_name, middle_name, gender, birth_date, email, phone)
    VALUES ('Жукова', 'Вера', 'Андреевна', 'F', '1991-09-09', 'zhukova.va@univ.ru', '+7 900 111-09-01')
    RETURNING person_id
), e AS (
    INSERT INTO employee (person_id, personnel_number, position_id, employment_rate, hire_date)
    SELECT person_id, 'Т-0901',
           (SELECT position_id FROM position WHERE title = 'Старший преподаватель'),
           1.00, CURRENT_DATE
    FROM p RETURNING employee_id
)
INSERT INTO teacher (employee_id, department_id, degree_id, teaching_since)
SELECT employee_id,
       (SELECT department_id FROM department WHERE short_name = 'ИС'),
       (SELECT degree_id FROM academic_degree WHERE short_name = 'к.т.н.'),
       '2018-09-01'
FROM e;

-- READ
SELECT full_name, position, degree, academic_title, department, teaching_experience_years
FROM v_teachers ORDER BY department, full_name;
SELECT * FROM v_teacher_load ORDER BY total_hours DESC;

-- UPDATE: присвоено звание доцента, ставка 1.25
UPDATE teacher SET title_id = (SELECT title_id FROM academic_title WHERE name = 'Доцент')
WHERE employee_id = (SELECT employee_id FROM employee WHERE personnel_number = 'Т-0901');
UPDATE employee SET employment_rate = 1.25 WHERE personnel_number = 'Т-0901';

-- DELETE (логическое): увольнение — заполняется дата, из v_teachers пропадает
UPDATE employee SET dismissal_date = CURRENT_DATE WHERE personnel_number = 'Т-0901';

-- =====================================================================
-- СОТРУДНИКИ ДЕКАНАТА
-- =====================================================================
WITH p AS (
    INSERT INTO person (last_name, first_name, gender, birth_date, email)
    VALUES ('Тихонова', 'Алина', 'F', '1999-02-02', 'tikhonova.a@univ.ru')
    RETURNING person_id
), e AS (
    INSERT INTO employee (person_id, personnel_number, position_id, hire_date)
    SELECT person_id, 'Т-0902', (SELECT position_id FROM position WHERE title = 'Методист'), CURRENT_DATE
    FROM p RETURNING employee_id
)
INSERT INTO dean_office_staff (employee_id, institute_id, office_room, duties)
SELECT employee_id, 1, 'А-214', 'Военно-учётный стол' FROM e;

SELECT personnel_number, full_name, position, unit FROM v_employees ORDER BY category, full_name;

UPDATE dean_office_staff SET duties = 'Военно-учётный стол, справки для военкомата'
WHERE employee_id = (SELECT employee_id FROM employee WHERE personnel_number = 'Т-0902');

DELETE FROM employee WHERE personnel_number = 'Т-0902';   -- каскадом удалит dean_office_staff

-- =====================================================================
-- ГРУППЫ: шифр по правилам СТАНКИНа, учебные планы, расписание
-- =====================================================================
-- Какой шифр должна иметь новая группа набора 2026 года по программной инженерии
SELECT fn_group_prefix(curriculum_id) || '16' AS new_group_name
FROM study_group WHERE name = 'ИДБ-26-11';

INSERT INTO study_group (name, curriculum_id, curator_id)
VALUES ('ИДБ-26-16',
        (SELECT curriculum_id FROM study_group WHERE name = 'ИДБ-26-11'),
        (SELECT employee_id FROM employee WHERE personnel_number = 'Т-0007'));

SELECT * FROM v_groups WHERE group_name LIKE 'ИДБ-26-%' ORDER BY group_name;
SELECT weekday_name, pair_number, start_time, week, discipline, lesson_type, teacher, classroom
FROM v_schedule WHERE group_name = 'ИДБ-24-13' ORDER BY weekday, pair_number;

-- Учебный план направления по семестрам с часами и ЗЕТ
SELECT semester, discipline, control_type, total_hours, credits
FROM v_curriculum WHERE specialty LIKE '09.03.04%' AND start_year = 2023 ORDER BY semester, discipline;

-- Перенос практики ИДБ-24-13 в свободную в то же время аудиторию (проверка накладок сработает сама)
UPDATE schedule_slot ss
SET classroom_id = (SELECT c.classroom_id FROM classroom c
                    WHERE c.room_kind = 'practice' AND c.classroom_id <> ss.classroom_id
                      AND NOT EXISTS (SELECT 1 FROM schedule_slot o
                                      WHERE o.classroom_id = c.classroom_id
                                        AND o.weekday = ss.weekday AND o.pair_number = ss.pair_number)
                    ORDER BY c.classroom_id LIMIT 1)
WHERE ss.slot_id = (SELECT MIN(s2.slot_id) FROM schedule_slot s2
                    JOIN teaching_assignment ta USING (assignment_id)
                    WHERE ta.group_id = (SELECT group_id FROM study_group WHERE name = 'ИДБ-24-13')
                      AND ta.lesson_type_id = 2)
RETURNING slot_id, weekday, pair_number, classroom_id;

UPDATE study_group SET is_archived = TRUE WHERE name = 'ИДБ-26-16';

-- =====================================================================
-- УСПЕВАЕМОСТЬ: модули, экзамены, пересдачи
-- =====================================================================
-- Успеваемость студента: баллы за модули и за зачёт / экзамен
SELECT semester, discipline, module_1, module_2, credit AS "зачёт", exam AS "экзамен", final_result
FROM v_performance WHERE student = 'Иванов Кирилл Сергеевич' ORDER BY semester, discipline;

-- Должники и кандидаты на отчисление
SELECT group_name, student, discipline, semester, attempts, last_result
FROM v_debtors ORDER BY group_name, student;
SELECT * FROM v_expulsion_risk ORDER BY group_name, student;

-- Сидоров сдаёт пересдачу по машинному обучению (ведомость на октябрь уже открыта)
INSERT INTO grade (sheet_id, student_id, points)          -- 38 баллов = «хорошо»
SELECT gs.sheet_id, s.student_id, 38
FROM grade_sheet gs, student s
WHERE gs.sheet_number = 'ИДБ-23-11/6/' || lpad((SELECT discipline_id FROM discipline WHERE name = 'Машинное обучение')::TEXT, 2, '0') || '/П1'
  AND s.record_book_number = '23-1103';

-- Исправление оценки в открытой ведомости
UPDATE grade SET points = 47                               -- 47 баллов = «отлично»
WHERE student_id = (SELECT student_id FROM student WHERE record_book_number = '23-1103')
  AND sheet_id = (SELECT sheet_id FROM grade_sheet WHERE sheet_kind = 'retake' AND status = 'open'
                    AND group_id = (SELECT group_id FROM study_group WHERE name = 'ИДБ-23-11')
                    AND item_id IN (SELECT item_id FROM curriculum_item ci JOIN discipline d USING (discipline_id)
                                    WHERE d.name = 'Машинное обучение'));

SELECT * FROM v_grades WHERE student = 'Сидоров Никита Олегович' ORDER BY exam_date;

-- Отчисление кандидата после проваленной комиссии
DO $$
DECLARE v_id INT;
BEGIN
    SELECT student_id INTO v_id FROM student WHERE record_book_number = '24-1203';
    CALL sp_expel_student(v_id, '904-с/26', 'Невыполнение учебного плана: не ликвидирована академическая задолженность', 1);
END $$;

-- Триггеры защищают данные: изменить оценку в закрытой ведомости напрямую нельзя
-- (только по подтверждённой заявке, см. ниже), как и поставить баллы вне шкалы 25–54
DO $$
BEGIN
    BEGIN
        UPDATE grade SET points = 30
        WHERE student_id = (SELECT student_id FROM student WHERE record_book_number = '23-1101')
          AND sheet_id = (SELECT MIN(sheet_id) FROM grade_sheet WHERE status = 'closed');
    EXCEPTION WHEN OTHERS THEN RAISE NOTICE 'Ожидаемая ошибка: %', SQLERRM;
    END;
    BEGIN
        INSERT INTO grade (sheet_id, student_id, points)
        SELECT MIN(gs.sheet_id), (SELECT student_id FROM student WHERE record_book_number = '23-1104'), 20
        FROM grade_sheet gs WHERE gs.status = 'open';
    EXCEPTION WHEN OTHERS THEN RAISE NOTICE 'Ожидаемая ошибка: %', SQLERRM;
    END;
END $$;

-- Исправление оценки в ЗАКРЫТОЙ ведомости: заявка → решение директора или заместителя.
-- 1) Преподаватель-экзаменатор (пользователь novikov) подаёт заявку
SELECT set_config('app.user_id', (SELECT user_id FROM app_user WHERE login = 'novikov')::TEXT, TRUE);
SELECT fn_request_grade_correction(gs.sheet_number, s.record_book_number, g.points + 2, FALSE,
                                   'Не учтены баллы за доклад на семинаре (служебная записка)') AS correction_id
FROM grade g JOIN grade_sheet gs USING (sheet_id) JOIN student s USING (student_id)
WHERE gs.examiner_id = (SELECT e.employee_id FROM employee e JOIN app_user u USING (person_id) WHERE u.login = 'novikov')
  AND gs.status = 'closed' AND gs.sheet_kind = 'main' AND gs.stage = 'final' AND g.points BETWEEN 30 AND 40
  AND s.record_book_number = '23-1102'
ORDER BY gs.exam_date DESC LIMIT 1;
-- 2) Директор института (smirnov) подтверждает — оценка меняется в той же транзакции
SELECT set_config('app.user_id', (SELECT user_id FROM app_user WHERE login = 'smirnov')::TEXT, TRUE);
DO $$
DECLARE v_id INT := (SELECT MAX(correction_id) FROM grade_correction);
BEGIN
    CALL sp_decide_grade_correction(v_id, TRUE, 'Подтверждаю');
END $$;
SELECT sheet_number, student, old_result, new_result, current_result, requested_by, status, decided_by
FROM v_grade_corrections ORDER BY correction_id;
-- история: заявка, решение и само изменение оценки — в журнале
SELECT changed_at, table_name, changed_by, column_name, old_value, new_value
FROM v_audit WHERE table_name = 'grade_correction' OR (table_name = 'grade' AND operation = 'изменение')
ORDER BY audit_id;
SELECT set_config('app.user_id', '', TRUE);

SELECT * FROM v_student_rating ORDER BY avg_points DESC NULLS LAST LIMIT 10;

-- Расписание группы на неделю по датам и сверка часов с учебным планом
SELECT lesson_date, weekday_name, week, pair_number, start_time, discipline, lesson_type, teacher, classroom
FROM v_schedule_calendar WHERE group_name = 'ИДБ-23-11' AND lesson_date BETWEEN '2026-11-02' AND '2026-11-08'
ORDER BY lesson_date, pair_number;
SELECT * FROM v_schedule_hours WHERE group_name = 'ИДБ-23-11' ORDER BY discipline, lesson_type;

-- =====================================================================
-- ПОСЕЩАЕМОСТЬ
-- =====================================================================
SELECT student, group_name, lessons, absences, attendance_pct
FROM v_attendance_stats ORDER BY attendance_pct LIMIT 10;

UPDATE attendance SET is_present = FALSE, absence_reason = 'Справка о болезни'
WHERE student_id = (SELECT student_id FROM student WHERE record_book_number = '23-1102')
  AND lesson_date = (SELECT MIN(lesson_date) FROM attendance);

-- =====================================================================
-- СТИПЕНДИИ И ПРИКАЗЫ
-- =====================================================================
SELECT scholarship, count(*) AS students, sum(amount) AS per_month
FROM v_active_scholarships GROUP BY scholarship ORDER BY per_month DESC;

UPDATE scholarship_type SET amount = amount * 1.10;   -- индексация на 10 %

SELECT ot.name, COUNT(*) AS orders, SUM(cnt) AS students
FROM academic_order o
JOIN order_type ot ON ot.order_type_id = o.order_type_id
JOIN (SELECT order_id, COUNT(*) AS cnt FROM order_student GROUP BY order_id) os USING (order_id)
GROUP BY ot.name ORDER BY orders DESC;

-- =====================================================================
-- СПРАВКИ
-- =====================================================================
INSERT INTO document_request (student_id, document_type_id, request_status_id, copies, purpose)
SELECT student_id, 1, 1, 1, 'В банк' FROM student WHERE record_book_number = '23-1102';

SELECT * FROM v_document_queue ORDER BY due_date LIMIT 10;

-- Сотрудник берёт в работу, затем выдаёт (completed_at проставит триггер)
UPDATE document_request SET request_status_id = 2, processed_by_id = 4
WHERE purpose = 'В банк' AND student_id = (SELECT student_id FROM student WHERE record_book_number = '23-1102');
UPDATE document_request SET request_status_id = 4
WHERE purpose = 'В банк' AND student_id = (SELECT student_id FROM student WHERE record_book_number = '23-1102')
RETURNING request_id, completed_at;

-- =====================================================================
-- АКАДЕМИЧЕСКИЙ ОТПУСК: досрочный выход освобождает остаток периода
-- =====================================================================
DO $$
DECLARE v_id INT;
BEGIN
    SELECT student_id INTO v_id FROM student WHERE record_book_number = '23-1102';
    -- отпуск по заявлению с начала сентября (задним числом) на 270 дней
    CALL sp_grant_academic_leave(v_id, 'Семейные обстоятельства', CURRENT_DATE - 30, CURRENT_DATE + 240, '905-с/26', 1);
    -- досрочный выход сегодня: отпуск фактически закончился вчера
    CALL sp_return_from_leave(v_id, '906-с/26', 1);
    -- новый отпуск внутри прежнего планового периода — допустим, остаток свободен
    CALL sp_grant_academic_leave(v_id, 'Медицинские показания', CURRENT_DATE + 30, CURRENT_DATE + 200, '908-с/26', 1);
END $$;
SELECT student, reason, start_date, end_date, actual_end_date, state
FROM v_academic_leaves WHERE student LIKE 'Петрова Дарья%' ORDER BY start_date;
-- в отпуске сейчас, но выход уже назначен на начало весеннего семестра
SELECT student, group_name, start_date, end_date, actual_end_date, state FROM v_academic_leaves ORDER BY start_date;

-- =====================================================================
-- ПРИКАЗЫ НА БУДУЩЕЕ: исполнение в дату вступления в силу
-- Раз в сутки планировщик бэкенда (или pg_cron) вызывает:
-- =====================================================================
SELECT fn_apply_due_orders() AS applied_today;
SELECT student, order_number, order_type, current_status, due_date, days_left, new_group
FROM v_pending_orders ORDER BY due_date;

-- =====================================================================
-- ПРАКТИКА, КУРСОВЫЕ, ВКР, КОНТАКТЫ РОДИТЕЛЕЙ
-- =====================================================================
SELECT organization, count(*) AS students FROM v_practice GROUP BY 1 ORDER BY 2 DESC;
-- должность руководителя от организации меняется в одном месте, у всех его практикантов сразу
UPDATE organization_contact SET job_title = 'Руководитель отдела разработки'
WHERE org_contact_id = (SELECT MIN(org_contact_id) FROM organization_contact);

SELECT student, group_name, topic, supervisor, reviewer FROM v_academic_works WHERE work_kind = 'ВКР' ORDER BY group_name, student;
-- у кого из выпускников ещё нет утверждённой темы ВКР
SELECT s.full_name, s.group_name FROM v_students s
WHERE s.course = 4 AND s.status = 'Обучается'
  AND NOT EXISTS (SELECT 1 FROM v_academic_works w WHERE w.student = s.full_name AND w.work_kind = 'ВКР')
ORDER BY s.group_name, s.full_name;

-- новый контакт: сначала контактное лицо, затем связь со студентом
WITH cp AS (
    INSERT INTO contact_person (last_name, first_name, middle_name, phone)
    VALUES ('Сидорова', 'Ирина', 'Олеговна', '+7 916 300-99-08')
    RETURNING contact_person_id
)
INSERT INTO student_contact (student_id, contact_person_id, relation_id, is_emergency)
SELECT (SELECT student_id FROM student WHERE record_book_number = '23-1103'), contact_person_id,
       (SELECT relation_id FROM contact_relation WHERE name = 'Мать'), TRUE FROM cp;
-- один родитель у двух студентов (брат и сестра)
SELECT contact, relation, string_agg(student || ' (' || group_name || ')', ', ') AS children
FROM v_student_contacts GROUP BY contact, relation HAVING count(*) > 1;

-- =====================================================================
-- ЖУРНАЛ АГЕНТА И ЖУРНАЛ ИЗМЕНЕНИЙ
-- Перед изменениями в транзакции агент передаёт, от чьего имени и по какому
-- запросу он действует; триггер аудита запишет это автоматически.
-- =====================================================================
INSERT INTO agent_request (user_id, request_text, intent_code, status)
VALUES (3, 'Поменяй телефон Петровой Дарье на +7 916 777-66-55', NULL, 'pending');

SELECT set_config('app.user_id', '3', TRUE),
       set_config('app.agent_request_id', (SELECT MAX(agent_request_id)::TEXT FROM agent_request), TRUE);

UPDATE person SET phone = '+7 916 777-66-55'
WHERE person_id = (SELECT person_id FROM student WHERE record_book_number = '23-1102');

SELECT changed_at, table_name, record_key, operation, changed_by, agent_request, column_name, old_value, new_value
FROM v_audit WHERE table_name = 'person' ORDER BY audit_id DESC LIMIT 5;

SELECT COALESCE(ai.intent_code, 'не распознано') AS intent, ai.operation, ai.target_table,
       COUNT(*) AS requests, ROUND(AVG(ar.execution_ms)) AS avg_ms,
       COUNT(*) FILTER (WHERE ar.status = 'success') AS ok
FROM agent_request ar
LEFT JOIN agent_intent ai ON ai.intent_code = ar.intent_code
GROUP BY ai.intent_code, ai.operation, ai.target_table ORDER BY requests DESC;

ROLLBACK;
