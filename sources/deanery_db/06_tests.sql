-- =====================================================================
--  Автотесты правил целостности, расписания, аудита и прав доступа
--  Выполнять после 01–05 (от postgres). Все изменения откатываются.
--  Каждый тест печатает «✓ …»; если правило не сработало — скрипт
--  останавливается с ошибкой «✗ …».
--
--  Тесты не зависят от внутренних номеров записей: студентов ищут по
--  зачётке, группы — по шифру, ведомости — по группе, дисциплине и семестру.
--  Опорные студенты (см. generate_seed.py, PERSONAS):
--    23-1101 Иванов К.С.   ИДБ-23-11  отличник, староста, повышенная стипендия
--    23-1102 Петрова Д.А.  ИДБ-23-11  хорошистка, академическая стипендия
--    23-1103 Сидоров Н.О.  ИДБ-23-11  договор, долг по «Машинному обучению», пересдача в октябре
--    23-1104 Николаев А.Д. ИДБ-23-11  три долга весенней сессии — кандидат на отчисление
--    24-1105 Титов Е.В.    ИДБ-24-11  академический отпуск; досрочный выход запланирован на 08.02.2027
--    24-1203 Гусев И.П.    ИДБ-24-12  не сдал комиссию — кандидат на отчисление
--    25-1305 Абрамов Г.А.  ИДБ-25-13  отчислен за академическую задолженность
--    24-1102 Ковалёва С.Д. ИДБ-25-11  вышла из академа в группу следующего набора
--    26-1101 Орлов Д.М.    ИДБ-26-11  первокурсник, староста
-- =====================================================================
SET search_path TO deanery, public;
SET client_min_messages TO notice;
BEGIN;

-- ---------- помощники ----------
-- Ожидаем ошибку: выполняем команды и проверяем, что база их отвергла
CREATE FUNCTION pg_temp.expect_error(p_what TEXT, VARIADIC p_sql TEXT[]) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE q TEXT;
BEGIN
    BEGIN
        FOREACH q IN ARRAY p_sql LOOP EXECUTE q; END LOOP;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE '✓ %  →  %', p_what, SQLERRM;
        RETURN;
    END;
    RAISE EXCEPTION '✗ Правило не сработало: %', p_what;
END $$;

-- Ожидаем ошибку с определённым текстом (чтобы правило сработало по нужной причине)
CREATE FUNCTION pg_temp.expect_error_msg(p_what TEXT, p_fragment TEXT, VARIADIC p_sql TEXT[]) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE q TEXT;
BEGIN
    BEGIN
        FOREACH q IN ARRAY p_sql LOOP EXECUTE q; END LOOP;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT ILIKE '%' || p_fragment || '%' THEN
            RAISE EXCEPTION '✗ % — ошибка не та: %', p_what, SQLERRM;
        END IF;
        RAISE NOTICE '✓ %  →  %', p_what, SQLERRM;
        RETURN;
    END;
    RAISE EXCEPTION '✗ Правило не сработало: %', p_what;
END $$;

-- Ожидаем успех; последняя команда — проверка, возвращающая TRUE.
-- Изменения откатываются, роль возвращается.
CREATE FUNCTION pg_temp.expect_ok(p_what TEXT, VARIADIC p_sql TEXT[]) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE i INT; ok BOOLEAN;
BEGIN
    BEGIN
        FOR i IN 1 .. array_length(p_sql, 1) - 1 LOOP EXECUTE p_sql[i]; END LOOP;
        EXECUTE p_sql[array_length(p_sql, 1)] INTO ok;
        IF ok IS NOT TRUE THEN
            RAISE EXCEPTION USING ERRCODE = 'P0098', MESSAGE = 'проверка вернула ' || COALESCE(ok::TEXT, 'NULL');
        END IF;
        RAISE EXCEPTION USING ERRCODE = 'P0099';          -- откат всего, что сделали
    EXCEPTION
        WHEN SQLSTATE 'P0099' THEN RAISE NOTICE '✓ %', p_what; RETURN;
        WHEN OTHERS THEN RAISE EXCEPTION '✗ %  →  %', p_what, SQLERRM;
    END;
END $$;

-- Поиск записей по понятным ключам
CREATE FUNCTION pg_temp.st(p_rb TEXT) RETURNS INT LANGUAGE sql STABLE AS
$$ SELECT student_id FROM deanery.student WHERE record_book_number = p_rb $$;
CREATE FUNCTION pg_temp.pid(p_rb TEXT) RETURNS INT LANGUAGE sql STABLE AS
$$ SELECT person_id FROM deanery.student WHERE record_book_number = p_rb $$;
CREATE FUNCTION pg_temp.grp(p_name TEXT) RETURNS INT LANGUAGE sql STABLE AS
$$ SELECT group_id FROM deanery.study_group WHERE name = p_name $$;
CREATE FUNCTION pg_temp.cur(p_group TEXT) RETURNS INT LANGUAGE sql STABLE AS
$$ SELECT curriculum_id FROM deanery.study_group WHERE name = p_group $$;
CREATE FUNCTION pg_temp.item(p_group TEXT, p_disc TEXT, p_sem INT) RETURNS INT LANGUAGE sql STABLE AS $$
    SELECT ci.item_id FROM deanery.curriculum_item ci
    JOIN deanery.discipline d ON d.discipline_id = ci.discipline_id
    WHERE ci.curriculum_id = pg_temp.cur(p_group) AND d.name = p_disc AND ci.semester = p_sem $$;
CREATE FUNCTION pg_temp.sheet(p_group TEXT, p_disc TEXT, p_sem INT, p_stage TEXT DEFAULT 'final', p_kind TEXT DEFAULT 'main')
RETURNS INT LANGUAGE sql STABLE AS $$
    SELECT sheet_id FROM deanery.grade_sheet
    WHERE item_id = pg_temp.item(p_group, p_disc, p_sem) AND group_id = pg_temp.grp(p_group)
      AND stage = p_stage AND sheet_kind = p_kind ORDER BY sheet_id LIMIT 1 $$;
CREATE FUNCTION pg_temp.asg(p_group TEXT, p_disc TEXT, p_lesson TEXT) RETURNS INT LANGUAGE sql STABLE AS $$
    SELECT ta.assignment_id FROM deanery.teaching_assignment ta
    JOIN deanery.curriculum_item ci ON ci.item_id = ta.item_id
    JOIN deanery.discipline d ON d.discipline_id = ci.discipline_id
    JOIN deanery.lesson_type lt ON lt.lesson_type_id = ta.lesson_type_id
    WHERE ta.group_id = pg_temp.grp(p_group) AND d.name = p_disc AND lt.name = p_lesson
    ORDER BY ta.assignment_id LIMIT 1 $$;
CREATE FUNCTION pg_temp.teacher_of(p_asg INT) RETURNS INT LANGUAGE sql STABLE AS
$$ SELECT teacher_id FROM deanery.teaching_assignment WHERE assignment_id = p_asg $$;
CREATE FUNCTION pg_temp.room(p_name TEXT) RETURNS INT LANGUAGE sql STABLE AS
$$ SELECT classroom_id FROM deanery.classroom WHERE building || '-' || room_number = p_name $$;
CREATE FUNCTION pg_temp.slot(p_group TEXT, p_parity TEXT) RETURNS INT LANGUAGE sql STABLE AS $$
    SELECT ss.slot_id FROM deanery.schedule_slot ss
    JOIN deanery.teaching_assignment ta ON ta.assignment_id = ss.assignment_id
    WHERE ta.group_id = pg_temp.grp(p_group) AND ss.week_parity = p_parity AND ss.valid_to IS NULL
    ORDER BY ss.slot_id LIMIT 1 $$;
CREATE FUNCTION pg_temp.lesson_day(p_slot INT) RETURNS DATE LANGUAGE sql STABLE AS
$$ SELECT MIN(lesson_date) FROM deanery.v_schedule_calendar WHERE slot_id = p_slot $$;
CREATE FUNCTION pg_temp.order_of(p_rb TEXT, p_code TEXT) RETURNS INT LANGUAGE sql STABLE AS $$
    SELECT o.order_id FROM deanery.academic_order o
    JOIN deanery.order_type t ON t.order_type_id = o.order_type_id
    JOIN deanery.order_student os ON os.order_id = o.order_id
    WHERE os.student_id = pg_temp.st(p_rb) AND t.code = p_code ORDER BY o.order_id LIMIT 1 $$;
CREATE FUNCTION pg_temp.headman(p_group TEXT) RETURNS INT LANGUAGE sql STABLE AS
$$ SELECT headman_id FROM deanery.study_group WHERE name = p_group $$;
CREATE FUNCTION pg_temp.uid(p_login TEXT) RETURNS TEXT LANGUAGE sql STABLE AS
$$ SELECT user_id::TEXT FROM deanery.app_user WHERE login = p_login $$;
CREATE FUNCTION pg_temp.sheet_no(p_sheet INT) RETURNS TEXT LANGUAGE sql STABLE AS
$$ SELECT sheet_number FROM deanery.grade_sheet WHERE sheet_id = p_sheet $$;
CREATE FUNCTION pg_temp.ord(p_number TEXT) RETURNS INT LANGUAGE sql STABLE AS
$$ SELECT order_id FROM deanery.academic_order WHERE order_number = p_number $$;
CREATE FUNCTION pg_temp.val(p_sql TEXT) RETURNS INT LANGUAGE plpgsql AS
$$ DECLARE v INT; BEGIN EXECUTE p_sql INTO v; RETURN v; END $$;

-- Ведомость пересдачи по «Машинному обучению» ИДБ-23-11: назначена на октябрь, пока пустая
-- (в ней должники Сидоров и Николаев)

\echo '== Опорные данные на месте =='
SELECT pg_temp.expect_ok('Опорные студенты, группы и открытая пересдача найдены',
    $q$SELECT pg_temp.st('23-1101') IS NOT NULL AND pg_temp.st('23-1103') IS NOT NULL AND pg_temp.st('24-1105') IS NOT NULL
          AND pg_temp.st('25-1305') IS NOT NULL AND pg_temp.grp('ИДБ-24-11') IS NOT NULL
          AND pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake') IS NOT NULL$q$);

\echo '== Согласованность между таблицами =='
SELECT pg_temp.expect_error_msg('Оценка студенту не из группы ведомости', 'не из группы',
    $q$INSERT INTO grade (sheet_id, student_id, points)
       VALUES (pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake'), pg_temp.st('26-1101'), 30)$q$);
SELECT pg_temp.expect_error_msg('Ведомость по дисциплине чужого учебного плана', 'не входит в учебный план',
    $q$INSERT INTO grade_sheet (sheet_number, item_id, group_id, examiner_id, issue_date)
       VALUES ('T-1', pg_temp.item('ИДБ-23-11', 'Базы данных', 4), pg_temp.grp('ИДБ-24-11'), 6, CURRENT_DATE)$q$);
SELECT pg_temp.expect_error_msg('Учебное поручение по дисциплине чужого плана', 'не входит в учебный план',
    $q$INSERT INTO teaching_assignment (item_id, group_id, teacher_id, lesson_type_id)
       VALUES (pg_temp.item('ИДБ-25-13', 'Математический анализ', 1), pg_temp.grp('ИДБ-23-11'), 8, 1)$q$);
SELECT pg_temp.expect_error_msg('Посещаемость студенту из другой группы', 'не обучается в группе',
    $q$INSERT INTO attendance (slot_id, student_id, lesson_date, is_present)
       VALUES (pg_temp.slot('ИДБ-23-11', 'every'), pg_temp.st('26-1101'), pg_temp.lesson_day(pg_temp.slot('ИДБ-23-11', 'every')), TRUE)$q$);
SELECT pg_temp.expect_error_msg('Посещаемость в день, когда занятия нет', 'днём недели',
    $q$INSERT INTO attendance (slot_id, student_id, lesson_date, is_present)
       VALUES (pg_temp.slot('ИДБ-23-11', 'every'), pg_temp.st('23-1101'), pg_temp.lesson_day(pg_temp.slot('ИДБ-23-11', 'every')) + 1, TRUE)$q$);
SELECT pg_temp.expect_error_msg('Посещаемость в неделю, когда занятие не идёт (чётность)', 'неделям',
    $q$INSERT INTO attendance (slot_id, student_id, lesson_date, is_present)
       SELECT ss.slot_id, s.student_id, pg_temp.lesson_day(ss.slot_id) + 7, TRUE
       FROM schedule_slot ss JOIN teaching_assignment ta USING (assignment_id)
       JOIN student s ON s.group_id = ta.group_id AND s.status_id = 1
       WHERE ss.week_parity = 'odd' ORDER BY ss.slot_id, s.student_id LIMIT 1$q$);
SELECT pg_temp.expect_error_msg('Посещаемость студенту в академическом отпуске', 'не обучается',
    $q$INSERT INTO attendance (slot_id, student_id, lesson_date, is_present)
       VALUES (pg_temp.slot('ИДБ-24-11', 'every'), pg_temp.st('24-1105'), pg_temp.lesson_day(pg_temp.slot('ИДБ-24-11', 'every')), TRUE)$q$);
SELECT pg_temp.expect_error_msg('Староста из другой группы', 'старостой',
    $q$UPDATE study_group SET headman_id = pg_temp.st('26-1101') WHERE name = 'ИДБ-23-11'$q$);
SELECT pg_temp.expect_ok('Старосту снимает с должности перевод в другую группу',
    $q$CALL sp_transfer_student(pg_temp.st('23-1101'), 'ИДБ-23-12', 'T-TR', 1)$q$,
    $q$SELECT headman_id IS NULL FROM study_group WHERE name = 'ИДБ-23-11'$q$);
SELECT pg_temp.expect_error_msg('Заведующий кафедрой не с этой кафедры', 'Заведующий кафедрой',
    'SET CONSTRAINTS ALL IMMEDIATE',
    $q$UPDATE department SET head_id = 8 WHERE short_name = 'ИС'$q$);
SELECT pg_temp.expect_error_msg('Перевод заведующего на другую кафедру', 'Заведующий кафедрой',
    'SET CONSTRAINTS ALL IMMEDIATE',
    'UPDATE teacher SET department_id = 2 WHERE employee_id = 5');
SELECT pg_temp.expect_error_msg('Директор института на преподавательской должности', 'Директором института',
    'UPDATE institute SET director_id = 6 WHERE institute_id = 1');
SELECT pg_temp.expect_error_msg('Преподаватель на административной должности', 'непреподавательскую',
    'INSERT INTO teacher (employee_id, department_id) VALUES (2, 1)');
SELECT pg_temp.expect_error_msg('Преподаватель одновременно в деканате', 'преподавательскую',
    'INSERT INTO dean_office_staff (employee_id, institute_id) VALUES (6, 1)');
SELECT pg_temp.expect_error_msg('Смена должности преподавателя на методиста', 'преподаватель',
    'UPDATE employee SET position_id = 4 WHERE employee_id = 6');
SELECT pg_temp.expect_error_msg('Практика по строке плана, которая не практика', 'практика',
    $q$INSERT INTO practice_placement (student_id, item_id, supervisor_id, org_contact_id, start_date, end_date)
       VALUES (pg_temp.st('26-1101'), pg_temp.item('ИДБ-26-11', 'Информатика', 1), 6, 1, '2027-07-01', '2027-07-20')$q$);
SELECT pg_temp.expect_error_msg('Практика из чужого учебного плана', 'не входит в учебный план',
    $q$INSERT INTO practice_placement (student_id, item_id, supervisor_id, org_contact_id, start_date, end_date)
       VALUES (pg_temp.st('26-1101'), pg_temp.item('ИДБ-23-11', 'Производственная практика', 6), 6, 1, '2027-07-01', '2027-07-20')$q$);
SELECT pg_temp.expect_error_msg('Тема курсовой к экзаменационной дисциплине', 'курсовой работой или ВКР',
    $q$INSERT INTO academic_work (student_id, item_id, topic, supervisor_id)
       VALUES (pg_temp.st('23-1101'), pg_temp.item('ИДБ-23-11', 'Базы данных', 4), 'Тема', 6)$q$);
SELECT pg_temp.expect_error_msg('Пересекающиеся академические отпуска', 'пересекается',
    $q$INSERT INTO academic_leave (student_id, reason_id, order_id, start_date, end_date)
       VALUES (pg_temp.st('24-1105'), 1, pg_temp.order_of('24-1105', 'leave'), '2026-06-01', '2026-12-01')$q$);
SELECT pg_temp.expect_error('Академический отпуск дольше 2 лет',
    $q$INSERT INTO academic_leave (student_id, reason_id, order_id, start_date, end_date)
       VALUES (pg_temp.st('24-1105'), 1, pg_temp.order_of('24-1105', 'leave'), '2028-01-01', '2030-06-01')$q$);
SELECT pg_temp.expect_error_msg('Академический отпуск по приказу о зачислении', 'не является приказом',
    $q$INSERT INTO academic_leave (student_id, reason_id, order_id, start_date, end_date)
       VALUES (pg_temp.st('23-1101'), 1, pg_temp.order_of('23-1101', 'enroll'), '2027-01-01', '2027-06-01')$q$);
SELECT pg_temp.expect_error_msg('Та же стипендия на пересекающийся период', 'scholarship_no_overlap',
    $q$INSERT INTO scholarship (student_id, scholarship_type_id, order_id, start_date, end_date)
       VALUES (pg_temp.st('23-1101'), 2, pg_temp.order_of('23-1101', 'scholarship'), '2026-10-01', '2026-12-31')$q$);
SELECT pg_temp.expect_error_msg('Баллы ниже минимума шкалы (24)', 'вне шкалы',
    $q$INSERT INTO grade (sheet_id, student_id, points)
       VALUES (pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake'), pg_temp.st('23-1103'), 24)$q$);
SELECT pg_temp.expect_error_msg('Баллы выше максимума шкалы (55)', 'вне шкалы',
    $q$INSERT INTO grade (sheet_id, student_id, points)
       VALUES (pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake'), pg_temp.st('23-1103'), 55)$q$);
SELECT pg_temp.expect_error('Баллы и неявка одновременно',
    $q$INSERT INTO grade (sheet_id, student_id, points, is_absent)
       VALUES (pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake'), pg_temp.st('23-1103'), 30, TRUE)$q$);
SELECT pg_temp.expect_error_msg('Модульная ведомость по практике (у практики модулей нет)', 'модул',
    $q$INSERT INTO grade_sheet (sheet_number, item_id, group_id, examiner_id, stage, issue_date)
       VALUES ('X-M', pg_temp.item('ИДБ-23-11', 'Производственная практика', 6), pg_temp.grp('ИДБ-23-11'), 7, 'module_1', CURRENT_DATE)$q$);
SELECT pg_temp.expect_error_msg('Вторая основная ведомость на модуль 1', 'ux_one_main_sheet',
    $q$INSERT INTO grade_sheet (sheet_number, item_id, group_id, examiner_id, stage, issue_date)
       VALUES ('X-M2', pg_temp.item('ИДБ-23-11', 'Базы данных', 4), pg_temp.grp('ИДБ-23-11'), 6, 'module_1', CURRENT_DATE)$q$);
SELECT pg_temp.expect_error_msg('Пересечение диапазонов шкалы (34 → и «3», и «4»)', 'score_band_no_overlap',
    'UPDATE score_band SET max_points = 36 WHERE mark = 3');
SELECT pg_temp.expect_error_msg('Исправление оценки в закрытой ведомости', 'закрыта',
    $q$UPDATE grade SET points = 30 WHERE sheet_id = pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)
                                     AND student_id = pg_temp.st('23-1101')$q$);

\echo '== Приказы вступают в силу в свою дату =='
SELECT pg_temp.expect_ok('Отчисление с будущей даты: статус пока прежний, приказ в списке запланированных',
    $q$CALL sp_expel_student(pg_temp.st('23-1104'), 'F-1', 'По собственному желанию', 1, CURRENT_DATE + 10)$q$,
    $q$SELECT (SELECT status FROM v_students WHERE record_book_number = '23-1104') = 'Обучается'
          AND EXISTS (SELECT 1 FROM v_pending_orders WHERE record_book_number = '23-1104' AND order_number = 'F-1'
                      AND due_date = CURRENT_DATE + 10 AND days_left = 10)
          AND EXISTS (SELECT 1 FROM v_student_orders WHERE order_number = 'F-1' AND state LIKE 'Запланирован на %')$q$);
SELECT pg_temp.expect_ok('Перевод с будущей даты: группа пока прежняя, стипендия не тронута',
    $q$CALL sp_transfer_student(pg_temp.st('23-1102'), 'ИДБ-23-12', 'F-2', 1, CURRENT_DATE + 7)$q$,
    $q$SELECT (SELECT group_name FROM v_students WHERE record_book_number = '23-1102') = 'ИДБ-23-11'
          AND NOT EXISTS (SELECT 1 FROM scholarship WHERE student_id = pg_temp.st('23-1102') AND end_date <= CURRENT_DATE + 7)$q$);
SELECT pg_temp.expect_error_msg('Исполнить приказ раньше даты вступления в силу нельзя', 'раньше нельзя',
    $q$CALL sp_transfer_student(pg_temp.st('23-1102'), 'ИДБ-23-12', 'F-3', 1, CURRENT_DATE + 7)$q$,
    $q$SELECT fn_apply_order(pg_temp.ord('F-3'), pg_temp.st('23-1102'))$q$);
SELECT pg_temp.expect_ok('Ежедневный запуск без наступивших приказов ничего не меняет',
    $q$CALL sp_expel_student(pg_temp.st('23-1104'), 'F-4', 'По собственному желанию', 1, CURRENT_DATE + 10)$q$,
    $q$SELECT fn_apply_due_orders() = 0
          AND (SELECT status FROM v_students WHERE record_book_number = '23-1104') = 'Обучается'$q$);
SELECT pg_temp.expect_ok('Наступивший приказ исполняется в свою дату: статус и стипендия — по дату отчисления',
    -- «прошло время»: приказ подписан неделю назад и вступил в силу позавчера, а ежедневный запуск ещё не был
    $q$INSERT INTO academic_order (order_number, order_date, order_type_id, institute_id, signed_by_id, title, effective_date)
       SELECT 'F-5', CURRENT_DATE - 7, order_type_id, 1, 1, 'Об отчислении', CURRENT_DATE - 2 FROM order_type WHERE code = 'expel'$q$,
    $q$INSERT INTO order_student (order_id, student_id, reason) VALUES (pg_temp.ord('F-5'), pg_temp.st('23-1102'), 'По собственному желанию')$q$,
    $q$DO $d$ BEGIN
         IF (SELECT status FROM v_students WHERE record_book_number = '23-1102') <> 'Обучается' THEN
             RAISE EXCEPTION 'приказ исполнился раньше запуска';
         END IF;
       END $d$$q$,
    $q$DO $d$ BEGIN
         IF fn_apply_due_orders() <> 1 THEN RAISE EXCEPTION 'наступивший приказ не исполнен'; END IF;
       END $d$$q$,
    $q$SELECT (SELECT status FROM v_students WHERE record_book_number = '23-1102') = 'Отчислен'
          AND (SELECT bool_and(end_date = CURRENT_DATE - 2) FROM scholarship WHERE student_id = pg_temp.st('23-1102'))
          AND NOT EXISTS (SELECT 1 FROM v_pending_orders WHERE order_number = 'F-5')$q$);
SELECT pg_temp.expect_error_msg('Вторая операция, пока ждёт исполнения первая', 'запланированный приказ',
    $q$CALL sp_expel_student(pg_temp.st('23-1104'), 'F-6', 'По собственному желанию', 1, CURRENT_DATE + 10)$q$,
    $q$CALL sp_transfer_student(pg_temp.st('23-1104'), 'ИДБ-23-12', 'F-7', 1)$q$);
SELECT pg_temp.expect_error_msg('Второй запланированный приказ в обход процедур', 'запланированный приказ',
    $q$CALL sp_expel_student(pg_temp.st('23-1104'), 'F-8', 'По собственному желанию', 1, CURRENT_DATE + 10)$q$,
    $q$INSERT INTO academic_order (order_number, order_date, order_type_id, institute_id, signed_by_id, title, effective_date)
       SELECT 'F-9', CURRENT_DATE, order_type_id, 1, 1, 'О переводе', CURRENT_DATE + 3 FROM order_type WHERE code = 'transfer'$q$,
    $q$INSERT INTO order_student (order_id, student_id, new_group_id) VALUES (pg_temp.ord('F-9'), pg_temp.st('23-1104'), pg_temp.grp('ИДБ-23-12'))$q$);
SELECT pg_temp.expect_ok('Запланированный приказ можно отменить до даты вступления в силу',
    $q$CALL sp_expel_student(pg_temp.st('23-1104'), 'F-10', 'По собственному желанию', 1, CURRENT_DATE + 10)$q$,
    $q$DO $d$ DECLARE v INT := pg_temp.st('23-1104'); BEGIN CALL sp_cancel_scheduled_order('F-10', v); END $d$$q$,
    $q$SELECT pg_temp.ord('F-10') IS NULL AND NOT EXISTS (SELECT 1 FROM v_pending_orders WHERE record_book_number = '23-1104')$q$);
SELECT pg_temp.expect_error_msg('Исполненный приказ отменить нельзя', 'уже исполнен',
    $q$DO $d$ DECLARE v INT := pg_temp.st('25-1305'); n TEXT;
       BEGIN SELECT order_number INTO n FROM academic_order WHERE order_id = pg_temp.order_of('25-1305', 'expel');
             CALL sp_cancel_scheduled_order(n, v); END $d$$q$);
SELECT pg_temp.expect_error_msg('Приказ задним числом', 'задним числом',
    $q$CALL sp_expel_student(pg_temp.st('23-1104'), 'F-11', 'По собственному желанию', 1, CURRENT_DATE - 1)$q$);
SELECT pg_temp.expect_error_msg('Исполненный приказ нельзя удалить из истории', 'уже исполнен',
    $q$DELETE FROM order_student WHERE order_id = pg_temp.order_of('23-1101', 'enroll') AND student_id = pg_temp.st('23-1101')$q$);
SELECT pg_temp.expect_error_msg('Исполненный приказ нельзя переписать', 'уже исполнен',
    $q$UPDATE order_student SET new_group_id = pg_temp.grp('ИДБ-23-12')
       WHERE order_id = pg_temp.order_of('25-1305', 'expel') AND student_id = pg_temp.st('25-1305')$q$);
SELECT pg_temp.expect_error_msg('Дату вступления в силу нельзя сдвинуть, когда в приказе есть студенты', 'уже включены студенты',
    $q$UPDATE academic_order SET effective_date = effective_date + 30 WHERE order_id = pg_temp.order_of('25-1305', 'expel')$q$);
SELECT pg_temp.expect_ok('Зачисление с будущей даты: статус «Зачислен», оценки и посещаемость пока нельзя',
    $q$SELECT fn_enroll_student('Будущев','Иван',NULL,'M','2008-05-05',NULL,NULL,'ИДБ-26-11','Бюджет','26-1398','F-12',
                                CURRENT_DATE, 1, CURRENT_DATE + 20)$q$,
    $q$SELECT status = 'Зачислен' AND enrollment_date = CURRENT_DATE + 20 FROM v_students WHERE record_book_number = '26-1398'$q$);
SELECT pg_temp.expect_error_msg('Деканат не меняет статус студента в обход приказа', 'permission denied',
    'SET LOCAL ROLE deanery_staff', $q$UPDATE student SET status_id = 3 WHERE student_id = pg_temp.st('23-1104')$q$);
SELECT pg_temp.expect_error_msg('Агент не меняет группу студента в обход приказа', 'permission denied',
    'SET LOCAL ROLE deanery_agent', $q$UPDATE student SET group_id = pg_temp.grp('ИДБ-23-12') WHERE student_id = pg_temp.st('23-1104')$q$);
SELECT pg_temp.expect_error_msg('Агент не может сам отметить приказ исполненным', 'permission denied',
    'SET LOCAL ROLE deanery_agent',
    $q$INSERT INTO order_student (order_id, student_id, applied_at) VALUES (pg_temp.order_of('23-1101', 'enroll'), pg_temp.st('23-1104'), now())$q$);
SELECT pg_temp.expect_ok('Агент планирует перевод с будущей даты',
    'SET LOCAL ROLE deanery_agent',
    $q$CALL sp_transfer_student(pg_temp.st('23-1102'), 'ИДБ-23-12', 'F-13', 1, CURRENT_DATE + 3)$q$,
    $q$SELECT EXISTS (SELECT 1 FROM v_pending_orders WHERE order_number = 'F-13')$q$);
SELECT pg_temp.expect_ok('В тестовых данных: досрочный выход и перевод, запланированные на весенний семестр',
    $q$SELECT EXISTS (SELECT 1 FROM v_academic_leaves WHERE student = 'Титов Егор Вячеславович'
                      AND actual_end_date < end_date AND (state LIKE 'В отпуске, выход назначен%' OR state LIKE 'Вышел%'))
          AND EXISTS (SELECT 1 FROM v_student_orders WHERE order_type = 'Перевод' AND effective_date = '2027-02-08')$q$);

\echo '== Академ: досрочный выход освобождает остаток периода =='
SELECT pg_temp.expect_ok('Досрочный выход сегодня: отпуск закончился вчера, в остатке можно оформить новый',
    $q$CALL sp_return_from_leave(pg_temp.st('25-1407'), 'L-1', 1)$q$,
    $q$INSERT INTO academic_leave (student_id, reason_id, order_id, start_date, end_date)
       SELECT student_id, 1, order_id, CURRENT_DATE + 30, CURRENT_DATE + 120 FROM academic_leave
       WHERE student_id = pg_temp.st('25-1407')$q$,
    $q$SELECT (SELECT actual_end_date FROM v_academic_leaves WHERE student_id = pg_temp.st('25-1407') AND start_date < CURRENT_DATE) = CURRENT_DATE - 1
          AND (SELECT status FROM v_students WHERE record_book_number = '25-1407') = 'Обучается'$q$);
SELECT pg_temp.expect_error_msg('Без досрочного выхода тот же период занят', 'пересекается',
    $q$INSERT INTO academic_leave (student_id, reason_id, order_id, start_date, end_date)
       SELECT student_id, 1, order_id, CURRENT_DATE + 30, CURRENT_DATE + 120 FROM academic_leave
       WHERE student_id = pg_temp.st('25-1407')$q$);
SELECT pg_temp.expect_ok('Через процедуры: отпуск, досрочный выход и новый отпуск в освободившемся интервале',
    $q$CALL sp_grant_academic_leave(pg_temp.st('23-1102'), 'Семейные обстоятельства', CURRENT_DATE - 30, CURRENT_DATE + 240, 'L-2', 1)$q$,
    $q$CALL sp_return_from_leave(pg_temp.st('23-1102'), 'L-3', 1)$q$,
    $q$CALL sp_grant_academic_leave(pg_temp.st('23-1102'), 'Медицинские показания', CURRENT_DATE + 30, CURRENT_DATE + 200, 'L-4', 1)$q$,
    $q$SELECT (SELECT count(*) FROM v_academic_leaves WHERE student_id = pg_temp.st('23-1102')) = 2
          AND EXISTS (SELECT 1 FROM v_academic_leaves WHERE student_id = pg_temp.st('23-1102') AND state = 'Вышел досрочно'
                      AND actual_end_date = CURRENT_DATE - 1 AND end_date = CURRENT_DATE + 240)
          AND EXISTS (SELECT 1 FROM v_academic_leaves WHERE student_id = pg_temp.st('23-1102') AND state = 'Предстоит')
          AND (SELECT status FROM v_students WHERE record_book_number = '23-1102') = 'Обучается'
          AND EXISTS (SELECT 1 FROM v_pending_orders WHERE order_number = 'L-4' AND due_date = CURRENT_DATE + 30)$q$);
SELECT pg_temp.expect_ok('Выход, назначенный на будущее: студент пока в отпуске, остаток уже освобождён',
    $q$CALL sp_grant_academic_leave(pg_temp.st('23-1102'), 'Семейные обстоятельства', CURRENT_DATE - 30, CURRENT_DATE + 240, 'L-5', 1)$q$,
    $q$CALL sp_return_from_leave(pg_temp.st('23-1102'), 'L-6', 1, CURRENT_DATE + 60)$q$,
    $q$SELECT (SELECT status FROM v_students WHERE record_book_number = '23-1102') = 'Академический отпуск'
          AND EXISTS (SELECT 1 FROM v_academic_leaves WHERE student_id = pg_temp.st('23-1102')
                      AND actual_end_date = CURRENT_DATE + 59 AND state LIKE 'В отпуске, выход назначен на %')$q$);
SELECT pg_temp.expect_error_msg('Выход в день начала отпуска', 'позже его начала',
    $q$CALL sp_grant_academic_leave(pg_temp.st('23-1102'), 'Семейные обстоятельства', CURRENT_DATE, CURRENT_DATE + 100, 'L-7', 1)$q$,
    $q$CALL sp_return_from_leave(pg_temp.st('23-1102'), 'L-8', 1)$q$);
SELECT pg_temp.expect_ok('Отмена запланированного выхода возвращает плановое окончание',
    $q$CALL sp_grant_academic_leave(pg_temp.st('23-1102'), 'Семейные обстоятельства', CURRENT_DATE - 30, CURRENT_DATE + 240, 'L-9', 1)$q$,
    $q$CALL sp_return_from_leave(pg_temp.st('23-1102'), 'L-10', 1, CURRENT_DATE + 60)$q$,
    $q$DO $d$ DECLARE v INT := pg_temp.st('23-1102'); BEGIN CALL sp_cancel_scheduled_order('L-10', v); END $d$$q$,
    $q$SELECT actual_end_date = CURRENT_DATE + 240 FROM v_academic_leaves WHERE student_id = pg_temp.st('23-1102')$q$);

\echo '== Приказ проверяется по студенту, типу и институту =='
SELECT pg_temp.expect_error_msg('Стипендия по приказу, в который студент не включён', 'не включён',
    $q$INSERT INTO scholarship (student_id, scholarship_type_id, order_id, start_date, end_date)
       VALUES (pg_temp.st('23-1103'), 4, pg_temp.order_of('23-1101', 'scholarship'), '2026-09-01', '2027-01-31')$q$);
SELECT pg_temp.expect_error_msg('Стипендия по приказу о зачислении', 'не является приказом',
    $q$INSERT INTO scholarship (student_id, scholarship_type_id, order_id, start_date, end_date)
       VALUES (pg_temp.st('23-1101'), 3, pg_temp.order_of('23-1101', 'enroll'), '2027-02-01', '2027-06-30')$q$);
SELECT pg_temp.expect_error_msg('Направление на практику по приказу о темах ВКР', 'не является приказом',
    $q$UPDATE practice_placement SET order_id = pg_temp.order_of('23-1101', 'thesis_topics') WHERE student_id = pg_temp.st('23-1101')$q$);
SELECT pg_temp.expect_error_msg('Тема курсовой по приказу, где студента нет', 'не включён',
    $q$UPDATE academic_work SET order_id = pg_temp.order_of('23-1101', 'thesis_topics')
       WHERE work_id = (SELECT MIN(work_id) FROM academic_work WHERE order_id IS NULL)$q$);
SELECT pg_temp.expect_error_msg('Выход из отпуска по приказу о предоставлении отпуска', 'не является приказом',
    $q$UPDATE academic_leave SET return_order_id = order_id WHERE student_id = pg_temp.st('24-1105')$q$);
SELECT pg_temp.expect_error_msg('Студент ИИТ в приказе другого института', 'издан институтом ИСТМ',
    $q$INSERT INTO academic_order (order_number, order_date, order_type_id, institute_id, signed_by_id, title, effective_date)
       SELECT 'I-1', CURRENT_DATE, order_type_id, 2, 13, 'Об отчислении', CURRENT_DATE + 5 FROM order_type WHERE code = 'expel'$q$,
    $q$INSERT INTO order_student (order_id, student_id, reason) VALUES (pg_temp.ord('I-1'), pg_temp.st('23-1104'), 'тест')$q$);
SELECT pg_temp.expect_error_msg('Стипендия по приказу другого института', 'издан институтом ИСТМ',
    $q$INSERT INTO academic_order (order_number, order_date, order_type_id, institute_id, signed_by_id, title, effective_date)
       SELECT 'I-2', CURRENT_DATE, order_type_id, 2, 13, 'О стипендии', CURRENT_DATE FROM order_type WHERE code = 'scholarship'$q$,
    'ALTER TABLE order_student DISABLE TRIGGER order_student_check',
    $q$INSERT INTO order_student (order_id, student_id, reason) VALUES (pg_temp.ord('I-2'), pg_temp.st('23-1103'), 'тест')$q$,
    'ALTER TABLE order_student ENABLE TRIGGER order_student_check',
    $q$INSERT INTO scholarship (student_id, scholarship_type_id, order_id, start_date, end_date)
       VALUES (pg_temp.st('23-1103'), 4, pg_temp.ord('I-2'), '2027-02-01', '2027-06-30')$q$);
SELECT pg_temp.expect_error_msg('Приказ ИИТ подписывает директор другого института', 'руководитель этого института',
    $q$INSERT INTO academic_order (order_number, order_date, order_type_id, institute_id, signed_by_id, title, effective_date)
       SELECT 'I-3', CURRENT_DATE, order_type_id, 1, 13, 'Об отчислении', CURRENT_DATE FROM order_type WHERE code = 'expel'$q$);
SELECT pg_temp.expect_error_msg('Приказ о переводе без новой группы', 'новую группу',
    $q$INSERT INTO academic_order (order_number, order_date, order_type_id, institute_id, signed_by_id, title, effective_date)
       SELECT 'I-4', CURRENT_DATE, order_type_id, 1, 1, 'О переводе', CURRENT_DATE + 5 FROM order_type WHERE code = 'transfer'$q$,
    $q$INSERT INTO order_student (order_id, student_id) VALUES (pg_temp.ord('I-4'), pg_temp.st('23-1104'))$q$);
SELECT pg_temp.expect_error_msg('Включить в приказ об отчислении уже отчисленного', 'в статусе «Отчислен»',
    $q$INSERT INTO academic_order (order_number, order_date, order_type_id, institute_id, signed_by_id, title, effective_date)
       SELECT 'I-5', CURRENT_DATE, order_type_id, 1, 1, 'Об отчислении', CURRENT_DATE + 5 FROM order_type WHERE code = 'expel'$q$,
    $q$INSERT INTO order_student (order_id, student_id) VALUES (pg_temp.ord('I-5'), pg_temp.st('25-1305'))$q$);

\echo '== Исправление оценки в закрытой ведомости =='
SELECT pg_temp.expect_error_msg('Заявка без указания пользователя', 'Не указан пользователь',
    $q$SELECT fn_request_grade_correction(pg_temp.sheet_no(pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)), '23-1101', 50, FALSE, 'Ошибка переноса')$q$);
SELECT pg_temp.expect_ok('Заявка методиста, одобрение директора: оценка исправлена, всё в журнале',
    $q$SELECT set_config('app.user_id', pg_temp.uid('popova'), TRUE)$q$,
    $q$SELECT fn_request_grade_correction(pg_temp.sheet_no(pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)), '23-1101', 50, FALSE,
                                          'Ошибка при переносе баллов из журнала')$q$,
    $q$SELECT set_config('app.user_id', pg_temp.uid('smirnov'), TRUE)$q$,
    $q$DO $d$ DECLARE v INT := (SELECT MAX(correction_id) FROM grade_correction);
       BEGIN CALL sp_decide_grade_correction(v, TRUE, 'Подтверждаю'); END $d$$q$,
    $q$SELECT (SELECT points FROM grade WHERE sheet_id = pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4) AND student_id = pg_temp.st('23-1101')) = 50
          AND EXISTS (SELECT 1 FROM v_grade_corrections WHERE record_book_number = '23-1101' AND old_result = '52'
                      AND new_result = '50' AND current_result = '50' AND status = 'Одобрена и применена'
                      AND requested_by = 'popova' AND decided_by = 'smirnov')
          AND EXISTS (SELECT 1 FROM v_audit WHERE table_name = 'grade' AND column_name = 'points'
                      AND old_value = '52' AND new_value = '50' AND changed_by = 'smirnov')
          AND EXISTS (SELECT 1 FROM v_audit WHERE table_name = 'grade_correction' AND column_name = 'status' AND new_value = 'applied')$q$);
SELECT pg_temp.expect_ok('Отклонение заявки: оценка не меняется',
    $q$SELECT set_config('app.user_id', pg_temp.uid('popova'), TRUE)$q$,
    $q$SELECT fn_request_grade_correction(pg_temp.sheet_no(pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)), '23-1101', 30, FALSE, 'Тест')$q$,
    $q$SELECT set_config('app.user_id', pg_temp.uid('kuznetsova'), TRUE)$q$,
    $q$DO $d$ DECLARE v INT := (SELECT MAX(correction_id) FROM grade_correction);
       BEGIN CALL sp_decide_grade_correction(v, FALSE, 'Основание не подтверждено журналом'); END $d$$q$,
    $q$SELECT (SELECT points FROM grade WHERE sheet_id = pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4) AND student_id = pg_temp.st('23-1101')) = 52
          AND (SELECT status FROM grade_correction ORDER BY correction_id DESC LIMIT 1) = 'rejected'$q$);
SELECT pg_temp.expect_error_msg('Автор заявки сам её подтверждает', 'Автор заявки',
    $q$SELECT set_config('app.user_id', pg_temp.uid('kuznetsova'), TRUE)$q$,
    $q$SELECT fn_request_grade_correction(pg_temp.sheet_no(pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)), '23-1101', 50, FALSE, 'Тест')$q$,
    $q$DO $d$ DECLARE v INT := (SELECT MAX(correction_id) FROM grade_correction);
       BEGIN CALL sp_decide_grade_correction(v, TRUE); END $d$$q$);
SELECT pg_temp.expect_error_msg('Подтверждает не руководитель института (методист)', 'директор или заместитель',
    $q$SELECT set_config('app.user_id', pg_temp.uid('kuznetsova'), TRUE)$q$,
    $q$SELECT fn_request_grade_correction(pg_temp.sheet_no(pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)), '23-1101', 50, FALSE, 'Тест')$q$,
    $q$SELECT set_config('app.user_id', pg_temp.uid('popova'), TRUE)$q$,
    $q$DO $d$ DECLARE v INT := (SELECT MAX(correction_id) FROM grade_correction);
       BEGIN CALL sp_decide_grade_correction(v, TRUE); END $d$$q$);
SELECT pg_temp.expect_error_msg('Подтверждает преподаватель', 'директор или заместитель',
    $q$SELECT set_config('app.user_id', pg_temp.uid('kuznetsova'), TRUE)$q$,
    $q$SELECT fn_request_grade_correction(pg_temp.sheet_no(pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)), '23-1101', 50, FALSE, 'Тест')$q$,
    $q$SELECT set_config('app.user_id', pg_temp.uid('novikov'), TRUE)$q$,
    $q$DO $d$ DECLARE v INT := (SELECT MAX(correction_id) FROM grade_correction);
       BEGIN CALL sp_decide_grade_correction(v, TRUE); END $d$$q$);
SELECT pg_temp.expect_error_msg('Повторное решение по уже рассмотренной заявке', 'уже рассмотрена',
    $q$SELECT set_config('app.user_id', pg_temp.uid('smirnov'), TRUE)$q$,
    $q$DO $d$ DECLARE v INT := (SELECT MIN(correction_id) FROM grade_correction WHERE status = 'applied');
       BEGIN CALL sp_decide_grade_correction(v, FALSE, 'Передумал'); END $d$$q$);
SELECT pg_temp.expect_error_msg('Отказ без объяснения', 'grade_correction',
    $q$SELECT set_config('app.user_id', pg_temp.uid('smirnov'), TRUE)$q$,
    $q$DO $d$ DECLARE v INT := (SELECT MIN(correction_id) FROM grade_correction WHERE status = 'pending');
       BEGIN CALL sp_decide_grade_correction(v, FALSE); END $d$$q$);
SELECT pg_temp.expect_error_msg('Преподаватель просит исправить чужую ведомость', 'только по своей ведомости',
    $q$SELECT set_config('app.user_id', pg_temp.uid('prokhorov'), TRUE)$q$,
    $q$SELECT fn_request_grade_correction(pg_temp.sheet_no(pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)), '23-1101', 50, FALSE, 'Тест')$q$);
SELECT pg_temp.expect_error_msg('Заявка по открытой ведомости не нужна', 'открыта',
    $q$SELECT set_config('app.user_id', pg_temp.uid('popova'), TRUE)$q$,
    $q$INSERT INTO grade (sheet_id, student_id, points)
       VALUES (pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake'), pg_temp.st('23-1103'), 30)$q$,
    $q$SELECT fn_request_grade_correction(pg_temp.sheet_no(pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake')),
                                          '23-1103', 35, FALSE, 'Тест')$q$);
SELECT pg_temp.expect_error_msg('Исправление попытки, после которой была пересдача', 'более поздняя попытка',
    $q$SELECT set_config('app.user_id', pg_temp.uid('popova'), TRUE)$q$,
    $q$SELECT fn_request_grade_correction(pg_temp.sheet_no(pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)), '23-1103', 30, FALSE, 'Тест')$q$);
SELECT pg_temp.expect_error_msg('Исправление на баллы вне шкалы', 'вне шкалы',
    $q$SELECT set_config('app.user_id', pg_temp.uid('popova'), TRUE)$q$,
    $q$SELECT fn_request_grade_correction(pg_temp.sheet_no(pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)), '23-1101', 60, FALSE, 'Тест')$q$);
SELECT pg_temp.expect_error_msg('Две заявки в работе по одной оценке', 'ux_one_open_correction',
    $q$SELECT set_config('app.user_id', pg_temp.uid('popova'), TRUE)$q$,
    $q$SELECT fn_request_grade_correction(pg_temp.sheet_no(pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)), '23-1101', 50, FALSE, 'Тест')$q$,
    $q$SELECT fn_request_grade_correction(pg_temp.sheet_no(pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)), '23-1101', 49, FALSE, 'Тест')$q$);
SELECT pg_temp.expect_error_msg('Заявку после подачи переписать нельзя', 'менять нельзя',
    $q$UPDATE grade_correction SET new_points = 54 WHERE status = 'pending'$q$);
SELECT pg_temp.expect_error_msg('Преподаватель не пишет в таблицу заявок напрямую', 'permission denied',
    'SET LOCAL ROLE deanery_teacher',
    $q$UPDATE grade_correction SET status = 'approved' WHERE status = 'pending'$q$);
SELECT pg_temp.expect_error_msg('Агент не принимает решений по исправлению оценок', 'permission denied',
    'SET LOCAL ROLE deanery_agent',
    $q$CALL sp_decide_grade_correction(1, TRUE)$q$);
SELECT pg_temp.expect_ok('Преподаватель подаёт заявку по своей ведомости',
    $q$SELECT set_config('app.user_id', pg_temp.uid('prokhorov'), TRUE)$q$,
    'SET LOCAL ROLE deanery_teacher',
    $q$SELECT fn_request_grade_correction(gs.sheet_number, s.record_book_number, 54, FALSE, 'Тест')
       FROM grade g JOIN grade_sheet gs USING (sheet_id) JOIN student s USING (student_id)
       WHERE gs.examiner_id = 24 AND gs.status = 'closed' AND g.points < 54
         AND NOT EXISTS (SELECT 1 FROM grade_correction c WHERE c.grade_id = g.grade_id)
       ORDER BY g.grade_id DESC LIMIT 1$q$,
    $q$SELECT EXISTS (SELECT 1 FROM v_grade_corrections WHERE requested_by = 'prokhorov' AND status = 'Ждёт решения' AND new_result = '54')$q$);
SELECT pg_temp.expect_ok('В тестовых данных: одна применённая заявка и одна ждёт решения',
    $q$SELECT count(*) FILTER (WHERE status = 'Одобрена и применена') = 1 AND count(*) FILTER (WHERE status = 'Ждёт решения') = 1
       FROM v_grade_corrections$q$);

\echo '== Шифр группы по правилам СТАНКИНа =='
SELECT pg_temp.expect_ok('Ожидаемое начало шифра для плана: ИДБ-26- и ИДМ-26-',
    $q$SELECT fn_group_prefix(pg_temp.cur('ИДБ-26-11')) = 'ИДБ-26-' AND fn_group_prefix(pg_temp.cur('ИДМ-26-06')) = 'ИДМ-26-'$q$);
SELECT pg_temp.expect_ok('Новая группа по правилам: ИДБ-25-16 и ИДМ-26-06(ИГ)',
    $q$INSERT INTO study_group (name, curriculum_id) VALUES ('ИДБ-25-16', pg_temp.cur('ИДБ-25-11'))$q$,
    $q$INSERT INTO study_group (name, curriculum_id) VALUES ('ИДМ-26-06(ИГ)', pg_temp.cur('ИДМ-26-06'))$q$,
    'SELECT TRUE');
SELECT pg_temp.expect_error_msg('Шифр с чужим годом набора', 'не соответствует учебному плану',
    $q$INSERT INTO study_group (name, curriculum_id) VALUES ('ИДБ-24-16', pg_temp.cur('ИДБ-25-11'))$q$);
SELECT pg_temp.expect_error_msg('Шифр магистратуры у группы бакалавриата', 'не соответствует учебному плану',
    $q$INSERT INTO study_group (name, curriculum_id) VALUES ('ИДМ-25-16', pg_temp.cur('ИДБ-25-11'))$q$);
SELECT pg_temp.expect_error_msg('Старое название вместо шифра — подсказка правильного начала', 'ожидается «ИДБ-25-NN»',
    $q$INSERT INTO study_group (name, curriculum_id) VALUES ('ПИ-25', pg_temp.cur('ИДБ-25-11'))$q$);
SELECT pg_temp.expect_error_msg('Номер группы не двузначный', 'study_group_name_check',
    $q$INSERT INTO study_group (name, curriculum_id) VALUES ('ИДБ-25-7', pg_temp.cur('ИДБ-25-11'))$q$);

\echo '== Расписание (проверки на свободной 6-й паре понедельника) =='
SELECT pg_temp.expect_error_msg('Аудитория занята другим преподавателем', 'аудитория',
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, term_id)
       VALUES (pg_temp.asg('ИДБ-26-11', 'Основы программирования', 'Лабораторная'), pg_temp.room('А-201'), 1, 6, 7)$q$,
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, term_id)
       SELECT ta.assignment_id, pg_temp.room('А-201'), 1, 6, 7 FROM teaching_assignment ta
       WHERE ta.group_id = pg_temp.grp('ИДБ-26-12') AND ta.lesson_type_id = 3
         AND ta.teacher_id <> pg_temp.teacher_of(pg_temp.asg('ИДБ-26-11', 'Основы программирования', 'Лабораторная'))
       ORDER BY ta.assignment_id LIMIT 1$q$);
SELECT pg_temp.expect_error_msg('Преподаватель уже ведёт занятие в это время', 'преподаватель',
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, term_id)
       VALUES (pg_temp.asg('ИДБ-26-11', 'Основы программирования', 'Лабораторная'), pg_temp.room('А-201'), 1, 6, 7)$q$,
    $q$INSERT INTO teaching_assignment (item_id, group_id, teacher_id, lesson_type_id, subgroup)
       VALUES (pg_temp.item('ИДБ-26-13', 'Основы программирования', 1), pg_temp.grp('ИДБ-26-13'),
               pg_temp.teacher_of(pg_temp.asg('ИДБ-26-11', 'Основы программирования', 'Лабораторная')), 3, 1)$q$,
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, term_id)
       VALUES ((SELECT MAX(assignment_id) FROM teaching_assignment), pg_temp.room('А-202'), 1, 6, 7)$q$);
SELECT pg_temp.expect_error_msg('У группы уже есть пара в это время', 'у группы',
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, term_id)
       VALUES (pg_temp.asg('ИДБ-26-11', 'Основы программирования', 'Лабораторная'), pg_temp.room('А-201'), 1, 6, 7)$q$,
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, term_id)
       VALUES (pg_temp.asg('ИДБ-26-11', 'Иностранный язык', 'Практика'), pg_temp.room('В-201'), 1, 6, 7)$q$);
SELECT pg_temp.expect_error_msg('В аудитории не хватает мест', 'мест',
    $q$UPDATE classroom SET capacity = 3 WHERE classroom_id = pg_temp.room('Б-301')$q$,
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, term_id)
       VALUES (pg_temp.asg('ИДБ-26-11', 'Иностранный язык', 'Практика'), pg_temp.room('Б-301'), 1, 6, 7)$q$);
SELECT pg_temp.expect_error_msg('Замена преподавателя в поручении создаёт ему накладку', 'преподаватель',
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, term_id)
       VALUES (pg_temp.asg('ИДБ-26-11', 'Основы программирования', 'Лабораторная'), pg_temp.room('А-201'), 1, 6, 7)$q$,
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, term_id)
       VALUES (pg_temp.asg('ИДБ-26-12', 'Иностранный язык', 'Практика'), pg_temp.room('В-202'), 1, 6, 7)$q$,
    $q$UPDATE teaching_assignment
       SET teacher_id = pg_temp.teacher_of(pg_temp.asg('ИДБ-26-11', 'Основы программирования', 'Лабораторная'))
       WHERE assignment_id = pg_temp.asg('ИДБ-26-12', 'Иностранный язык', 'Практика')$q$);
SELECT pg_temp.expect_ok('Нечётная и чётная неделя в одной аудитории не конфликтуют',
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, week_parity, term_id)
       VALUES (pg_temp.asg('ИДБ-26-11', 'Иностранный язык', 'Практика'), pg_temp.room('Б-301'), 1, 6, 'odd', 7)$q$,
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, week_parity, term_id)
       VALUES (pg_temp.asg('ИДБ-26-12', 'Иностранный язык', 'Практика'), pg_temp.room('Б-301'), 1, 6, 'even', 7)$q$,
    'SELECT TRUE');
SELECT pg_temp.expect_ok('Лекционный поток: одна лекция для двух групп в одной аудитории',
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, term_id)
       VALUES (pg_temp.asg('ИДБ-26-11', 'Математический анализ', 'Лекция'), pg_temp.room('А-101'), 1, 6, 7)$q$,
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, term_id)
       VALUES (pg_temp.asg('ИДБ-26-12', 'Математический анализ', 'Лекция'), pg_temp.room('А-101'), 1, 6, 7)$q$,
    'SELECT TRUE');

\echo '== Журнал изменений =='
SELECT pg_temp.expect_ok('Изменение записывается с пользователем и запросом агента',
    $q$SET LOCAL app.user_id = '2'$q$,
    $q$SET LOCAL app.agent_request_id = '1'$q$,
    $q$UPDATE person SET phone = '+7 999 000-00-00' WHERE person_id = pg_temp.pid('23-1101')$q$,
    $q$SELECT EXISTS (SELECT 1 FROM v_audit WHERE table_name = 'person' AND record_key = 'person_id=' || pg_temp.pid('23-1101')
                      AND changed_by = 'kuznetsova' AND column_name = 'phone'
                      AND new_value = '+7 999 000-00-00' AND agent_request IS NOT NULL)$q$);
SELECT pg_temp.expect_ok('Паспортные данные в журнале скрыты',
    $q$UPDATE person SET passport_number = '999999' WHERE person_id = pg_temp.pid('23-1101')$q$,
    $q$SELECT bool_and(old_value = '***' AND new_value = '***') FROM audit_log_detail WHERE column_name = 'passport_number'$q$);
SELECT pg_temp.expect_ok('Изменение без реальных отличий не засоряет журнал',
    $q$UPDATE student SET status_id = status_id WHERE student_id = pg_temp.st('23-1102')$q$,
    $q$SELECT NOT EXISTS (SELECT 1 FROM audit_log WHERE table_name = 'student'
                          AND record_key = 'student_id=' || pg_temp.st('23-1102'))$q$);
SELECT pg_temp.expect_error_msg('Журнал нельзя исправить', 'только для чтения',
    $q$UPDATE person SET phone = '+7 999 000-00-01' WHERE person_id = pg_temp.pid('23-1102')$q$,
    $q$UPDATE audit_log SET db_user = 'кто-то другой'$q$);
SELECT pg_temp.expect_error_msg('Из журнала нельзя удалить запись', 'только для чтения',
    $q$UPDATE person SET phone = '+7 999 000-00-01' WHERE person_id = pg_temp.pid('23-1102')$q$,
    'DELETE FROM audit_log');

\echo '== Права доступа: агент =='
SELECT pg_temp.expect_ok('Агент читает представления',
    'SET LOCAL ROLE deanery_agent',
    $q$SELECT count(*) > 400 FROM v_students$q$);
SELECT pg_temp.expect_error_msg('Агент не видит паспортные данные', 'permission denied',
    'SET LOCAL ROLE deanery_agent', 'SELECT passport_number FROM person');
SELECT pg_temp.expect_error_msg('Агент не видит хэши паролей', 'permission denied',
    'SET LOCAL ROLE deanery_agent', 'SELECT password_hash FROM app_user');
SELECT pg_temp.expect_error_msg('Агент не может удалять данные', 'permission denied',
    'SET LOCAL ROLE deanery_agent', 'DELETE FROM document_request');
SELECT pg_temp.expect_error_msg('Агент не может менять справочники', 'permission denied',
    'SET LOCAL ROLE deanery_agent', $q$UPDATE score_band SET min_points = 20 WHERE mark = 3$q$);
SELECT pg_temp.expect_error_msg('Агент не может менять учётные записи', 'permission denied',
    'SET LOCAL ROLE deanery_agent', 'UPDATE app_user SET role_id = 1');
SELECT pg_temp.expect_error_msg('Агент не может менять паспорт студента', 'permission denied',
    'SET LOCAL ROLE deanery_agent',
    $q$UPDATE person SET passport_number = '000000' WHERE person_id = pg_temp.pid('23-1101')$q$);
SELECT pg_temp.expect_error_msg('Агент не читает журнал напрямую', 'permission denied',
    'SET LOCAL ROLE deanery_agent', 'SELECT * FROM audit_log_detail');
SELECT pg_temp.expect_ok('Агент зачисляет студента процедурой',
    'SET LOCAL ROLE deanery_agent',
    $q$SELECT fn_enroll_student('Тестов','Тест',NULL,'M','2008-01-01','test@stud.univ.ru',NULL,
                                'ИДБ-25-13','Бюджет','25-1399','T-ENR',CURRENT_DATE,1)$q$,
    $q$SELECT EXISTS (SELECT 1 FROM v_students WHERE record_book_number = '25-1399' AND group_name = 'ИДБ-25-13')$q$);
SELECT pg_temp.expect_ok('Агент оформляет академический отпуск; изменения попадают в журнал',
    'SET LOCAL ROLE deanery_agent',
    $q$CALL sp_grant_academic_leave(pg_temp.st('23-1104'), 'Семейные обстоятельства', '2026-10-01', '2027-06-30', 'T-LV', 1)$q$,
    $q$SELECT EXISTS (SELECT 1 FROM v_academic_leaves WHERE student = 'Николаев Артём Денисович' AND group_name = 'ИДБ-23-11'
                      AND reason = 'Семейные обстоятельства')
          AND EXISTS (SELECT 1 FROM v_audit WHERE table_name = 'academic_leave')$q$);
SELECT pg_temp.expect_ok('Агент отчисляет кандидата на отчисление (не сдал комиссию)',
    'SET LOCAL ROLE deanery_agent',
    $q$CALL sp_expel_student(pg_temp.st('24-1203'), 'T-EX', 'Невыполнение учебного плана', 1)$q$,
    $q$SELECT status = 'Отчислен' FROM v_students WHERE record_book_number = '24-1203'$q$);
SELECT pg_temp.expect_ok('Агент переводит старосту (право на снятие старосты есть)',
    'SET LOCAL ROLE deanery_agent',
    $q$CALL sp_transfer_student(pg_temp.headman('ИДБ-24-11'), 'ИДБ-24-12', 'T-TR2', 1)$q$,
    $q$SELECT headman_id IS NULL FROM study_group WHERE name = 'ИДБ-24-11'$q$);

\echo '== Права доступа: другие роли =='
SELECT pg_temp.expect_ok('Преподаватель ставит оценку',
    'SET LOCAL ROLE deanery_teacher',
    $q$INSERT INTO grade (sheet_id, student_id, points)
       VALUES (pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake'), pg_temp.st('23-1103'), 30)$q$,
    'SELECT TRUE');
SELECT pg_temp.expect_error_msg('Преподаватель не меняет данные студентов', 'permission denied',
    'SET LOCAL ROLE deanery_teacher', 'UPDATE student SET funding_type_id = 1');
SELECT pg_temp.expect_error_msg('Роль «только чтение» ничего не меняет', 'permission denied',
    'SET LOCAL ROLE deanery_read',
    $q$INSERT INTO document_request (student_id, document_type_id, request_status_id) VALUES (pg_temp.st('23-1101'), 1, 1)$q$);
SELECT pg_temp.expect_ok('Деканат видит журнал изменений',
    'SET LOCAL ROLE deanery_staff',
    'SELECT count(*) >= 0 FROM audit_log');

\echo '== Защита от неверного ввода =='
SELECT pg_temp.expect_error_msg('Пустая фамилия', 'blank',
    $q$INSERT INTO person (last_name, first_name, gender, birth_date) VALUES ('', 'Иван', 'M', '2000-01-01')$q$);
SELECT pg_temp.expect_error_msg('Фамилия из одних пробелов', 'blank',
    $q$INSERT INTO person (last_name, first_name, gender, birth_date) VALUES ('   ', 'Иван', 'M', '2000-01-01')$q$);
SELECT pg_temp.expect_error('Пробелы по краям шифра группы',
    $q$UPDATE study_group SET name = ' ИДБ-23-11' WHERE name = 'ИДБ-23-11'$q$);
SELECT pg_temp.expect_error_msg('Дата рождения в будущем', 'в будущем',
    $q$INSERT INTO person (last_name, first_name, gender, birth_date) VALUES ('Тест', 'Иван', 'M', '2030-01-01')$q$);
SELECT pg_temp.expect_error_msg('Email, отличающийся от существующего только регистром', 'ux_person_email',
    $q$INSERT INTO person (last_name, first_name, gender, birth_date, email) VALUES ('Тест', 'Иван', 'M', '2000-01-01', 'IVANOV.KS@stud.univ.ru')$q$);
SELECT pg_temp.expect_error_msg('Логин, отличающийся от существующего только регистром', 'ux_app_user_login',
    $q$INSERT INTO app_user (person_id, role_id, login, password_hash) VALUES (pg_temp.pid('23-1103'), 5, 'Smirnov', 'x')$q$);
SELECT pg_temp.expect_error_msg('Дата зачисления раньше рождения', 'зачисления',
    $q$UPDATE student SET enrollment_date = '1990-01-01' WHERE student_id = pg_temp.st('23-1101')$q$);
SELECT pg_temp.expect_error_msg('Дата приёма на работу раньше рождения', 'приёма',
    $q$UPDATE employee SET hire_date = '1950-01-01' WHERE employee_id = 10$q$);
SELECT pg_temp.expect_error_msg('Семестр 12 в четырёхлетнем плане', 'семестров',
    $q$INSERT INTO curriculum_item (curriculum_id, discipline_id, semester, control_type_id, self_study_hours)
       SELECT pg_temp.cur('ИДБ-23-11'), discipline_id, 12, 1, 36 FROM discipline WHERE name = 'Базы данных'$q$);
SELECT pg_temp.expect_error('Строка плана без единого часа',
    $q$INSERT INTO curriculum_item (curriculum_id, discipline_id, semester, control_type_id)
       SELECT pg_temp.cur('ИДБ-23-11'), discipline_id, 5, 1 FROM discipline WHERE name = 'Дискретная математика'$q$);
SELECT pg_temp.expect_error_msg('Зачисление с опечаткой в основе обучения', 'не найдена',
    $q$SELECT fn_enroll_student('Тест','Тест',NULL,'M','2007-01-01',NULL,NULL,'ИДБ-26-11','Бюджетт','X-9','X-9',CURRENT_DATE,1)$q$);

\echo '== Операции не в том состоянии =='
SELECT pg_temp.expect_error_msg('Повторное отчисление отчисленного', 'в статусе «Отчислен»',
    $q$CALL sp_expel_student(pg_temp.st('25-1305'), 'X-1', 'повторно', 1)$q$);
SELECT pg_temp.expect_error_msg('Перевод в ту же группу', 'уже учится',
    $q$CALL sp_transfer_student(pg_temp.st('23-1102'), 'ИДБ-23-11', 'X-2', 1)$q$);
SELECT pg_temp.expect_error_msg('Перевод отчисленного', 'в статусе «Отчислен»',
    $q$CALL sp_transfer_student(pg_temp.st('25-1305'), 'ИДБ-25-12', 'X-3', 1)$q$);
SELECT pg_temp.expect_error_msg('Академический отпуск отчисленному', 'в статусе «Отчислен»',
    $q$CALL sp_grant_academic_leave(pg_temp.st('25-1305'), 'Медицинские показания', '2026-10-01', '2027-01-01', 'X-4', 1)$q$);
SELECT pg_temp.expect_error_msg('Выход из академа у того, кто в нём не был', 'в статусе «Обучается»',
    $q$CALL sp_return_from_leave(pg_temp.st('23-1102'), 'X-5', 1)$q$);
SELECT pg_temp.expect_error_msg('Восстановление того, кто не отчислен', 'в статусе «Обучается»',
    $q$CALL sp_reinstate_student(pg_temp.st('23-1102'), 'ИДБ-23-11', 'X-6', 1)$q$);
SELECT pg_temp.expect_error_msg('Перевод в архивную группу', 'в архиве',
    $q$INSERT INTO curriculum (program_id, study_form_id, start_year, duration_years)
       SELECT program_id, 1, 2019, 4 FROM curriculum WHERE curriculum_id = pg_temp.cur('ИДБ-23-11')$q$,
    $q$INSERT INTO study_group (name, curriculum_id, is_archived) VALUES ('ИДБ-19-11', (SELECT MAX(curriculum_id) FROM curriculum), TRUE)$q$,
    $q$CALL sp_transfer_student(pg_temp.st('23-1102'), 'ИДБ-19-11', 'X-7', 1)$q$);
SELECT pg_temp.expect_error_msg('Приказ подписал ассистент', 'подписать',
    'UPDATE academic_order SET signed_by_id = 10 WHERE order_id = (SELECT MIN(order_id) FROM academic_order)');
SELECT pg_temp.expect_error_msg('Оценка студенту в академическом отпуске', 'не обучается',
    $q$INSERT INTO grade (sheet_id, student_id, points)
       VALUES (pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake'), pg_temp.st('24-1105'), 30)$q$);
SELECT pg_temp.expect_error_msg('Пересдача студенту, который уже сдал', 'пересдача не нужна',
    $q$INSERT INTO grade (sheet_id, student_id, points)
       VALUES (pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake'), pg_temp.st('23-1101'), 30)$q$);
SELECT pg_temp.expect_error_msg('Вторая основная ведомость на ту же дисциплину', 'ux_one_main_sheet',
    $q$INSERT INTO grade_sheet (sheet_number, item_id, group_id, examiner_id, issue_date)
       VALUES ('X-8', pg_temp.item('ИДБ-23-11', 'Базы данных', 4), pg_temp.grp('ИДБ-23-11'), 6, CURRENT_DATE)$q$);
SELECT pg_temp.expect_error_msg('Повторное открытие закрытой ведомости', 'уже закрыта',
    $q$UPDATE grade_sheet SET status = 'open', closed_date = NULL WHERE sheet_id = pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)$q$);
SELECT pg_temp.expect_error_msg('Отмена закрытой ведомости', 'уже закрыта',
    $q$UPDATE grade_sheet SET status = 'cancelled', closed_date = NULL WHERE sheet_id = pg_temp.sheet('ИДБ-23-11', 'Базы данных', 4)$q$);
SELECT pg_temp.expect_error_msg('Закрытие пустой ведомости пересдачи', 'пустую',
    $q$UPDATE grade_sheet SET status = 'closed', closed_date = exam_date
       WHERE sheet_id = pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake')$q$);
SELECT pg_temp.expect_error_msg('Закрытие основной ведомости, где оценки есть не у всех', 'нет оценок',
    $q$INSERT INTO grade_sheet (sheet_number, item_id, group_id, examiner_id, issue_date, exam_date)
       VALUES ('X-9', pg_temp.item('ИДБ-23-11', 'Системы искусственного интеллекта', 7), pg_temp.grp('ИДБ-23-11'), 5, '2026-09-01', '2026-09-20')$q$,
    $q$INSERT INTO grade (sheet_id, student_id, points)
       VALUES ((SELECT sheet_id FROM grade_sheet WHERE sheet_number = 'X-9'), pg_temp.st('23-1101'), 50)$q$,
    $q$UPDATE grade_sheet SET status = 'closed', closed_date = '2026-09-22' WHERE sheet_number = 'X-9'$q$);
SELECT pg_temp.expect_error_msg('Посещаемость на будущую дату', 'будущую',
    $q$INSERT INTO attendance (slot_id, student_id, lesson_date, is_present)
       VALUES (pg_temp.slot('ИДБ-23-11', 'every'), pg_temp.st('23-1101'), CURRENT_DATE + 7, TRUE)$q$);
SELECT pg_temp.expect_error_msg('Учебное поручение уволенному преподавателю', 'уволен',
    'UPDATE employee SET dismissal_date = CURRENT_DATE WHERE employee_id = 9',
    $q$INSERT INTO teaching_assignment (item_id, group_id, teacher_id, lesson_type_id, subgroup)
       VALUES (pg_temp.item('ИДБ-26-11', 'Линейная алгебра и аналитическая геометрия', 1), pg_temp.grp('ИДБ-26-11'), 9, 2, 1)$q$);
SELECT pg_temp.expect_error_msg('Уволенный преподаватель в экзаменаторах', 'уволен',
    'UPDATE employee SET dismissal_date = CURRENT_DATE WHERE employee_id = 9',
    $q$UPDATE grade_sheet SET examiner_id = 9 WHERE sheet_id = pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake')$q$);
SELECT pg_temp.expect_error_msg('Увольнение директора института', 'директор института',
    'UPDATE employee SET dismissal_date = CURRENT_DATE WHERE employee_id = 1');
SELECT pg_temp.expect_error_msg('Увольнение заведующего кафедрой', 'заведующий кафедрой',
    'UPDATE employee SET dismissal_date = CURRENT_DATE WHERE employee_id = 12');
SELECT pg_temp.expect_error_msg('Увольнение куратора группы', 'куратор',
    'UPDATE employee SET dismissal_date = CURRENT_DATE WHERE employee_id = 10');
SELECT pg_temp.expect_error_msg('Архивирование группы с обучающимися', 'архивировать',
    $q$UPDATE study_group SET is_archived = TRUE WHERE name = 'ИДБ-23-11'$q$);
SELECT pg_temp.expect_error_msg('Государственная стипендия договорнику', 'на бюджете',
    $q$INSERT INTO scholarship (student_id, scholarship_type_id, order_id, start_date, end_date)
       VALUES (pg_temp.st('23-1103'), 1, pg_temp.order_of('23-1101', 'scholarship'), '2026-09-01', '2027-01-31')$q$);
SELECT pg_temp.expect_error_msg('Стипендия отчисленному', 'не обучается',
    $q$INSERT INTO scholarship (student_id, scholarship_type_id, order_id, start_date, end_date)
       VALUES (pg_temp.st('25-1305'), 4, pg_temp.order_of('23-1101', 'scholarship'), '2026-09-01', '2027-01-31')$q$);
SELECT pg_temp.expect_error_msg('Выданную справку вернуть в статус «Новая»', 'уже завершена',
    'UPDATE document_request SET request_status_id = 1 WHERE request_id = (SELECT MIN(request_id) FROM document_request WHERE request_status_id = 4)');
SELECT pg_temp.expect_error_msg('Удаление студента вместе со всей историей', 'foreign key',
    'SET LOCAL ROLE deanery_staff', $q$DELETE FROM student WHERE student_id = pg_temp.st('23-1101')$q$);
SELECT pg_temp.expect_error_msg('Удаление приказа вместе со списком студентов', 'foreign key',
    'DELETE FROM academic_order WHERE order_id = (SELECT MIN(order_id) FROM academic_order)');
SELECT pg_temp.expect_error_msg('Удаление занятия, по которому есть посещаемость', 'foreign key',
    'DELETE FROM schedule_slot WHERE slot_id = (SELECT MIN(slot_id) FROM attendance)');

\echo '== Правильные действия проходят =='
SELECT pg_temp.expect_ok('Восстановление отчисленного студента',
    $q$CALL sp_reinstate_student(pg_temp.st('25-1305'), 'ИДБ-26-13', 'X-10', 1)$q$,
    $q$SELECT status = 'Обучается' AND group_name = 'ИДБ-26-13' FROM v_students WHERE record_book_number = '25-1305'$q$);
SELECT pg_temp.expect_ok('Академ прекращает выплату стипендии',
    $q$CALL sp_grant_academic_leave(pg_temp.st('23-1102'), 'Семейные обстоятельства', '2026-10-01', '2027-06-30', 'X-11', 1)$q$,
    $q$SELECT count(*) > 0 AND bool_and(end_date = '2026-10-01') FROM scholarship WHERE student_id = pg_temp.st('23-1102')$q$);
SELECT pg_temp.expect_ok('Выход из академа возвращает статус «Обучается»',
    $q$CALL sp_grant_academic_leave(pg_temp.st('23-1102'), 'Семейные обстоятельства', CURRENT_DATE - 10, CURRENT_DATE + 200, 'X-12', 1)$q$,
    $q$CALL sp_return_from_leave(pg_temp.st('23-1102'), 'X-13', 1)$q$,
    $q$SELECT status = 'Обучается' FROM v_students WHERE record_book_number = '23-1102'$q$);
SELECT pg_temp.expect_ok('Переведённого с долгом можно внести в пересдачу старой группы',
    $q$CALL sp_transfer_student(pg_temp.st('23-1103'), 'ИДБ-23-12', 'X-14', 1)$q$,
    $q$INSERT INTO grade (sheet_id, student_id, points)
       VALUES (pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake'), pg_temp.st('23-1103'), 30)$q$,
    'SELECT TRUE');
SELECT pg_temp.expect_ok('Сданная пересдача убирает долг из списка должников',
    $q$INSERT INTO grade (sheet_id, student_id, points)
       VALUES (pg_temp.sheet('ИДБ-23-11', 'Машинное обучение', 6, 'final', 'retake'), pg_temp.st('23-1103'), 38)$q$,
    $q$SELECT NOT EXISTS (SELECT 1 FROM v_debtors WHERE student_id = pg_temp.st('23-1103') AND discipline = 'Машинное обучение')$q$);
SELECT pg_temp.expect_ok('Основная ведомость закрывается, когда оценки есть у всех',
    $q$INSERT INTO grade_sheet (sheet_number, item_id, group_id, examiner_id, issue_date, exam_date)
       VALUES ('X-15', pg_temp.item('ИДБ-23-11', 'Системы искусственного интеллекта', 7), pg_temp.grp('ИДБ-23-11'), 5, '2026-09-01', '2026-09-20')$q$,
    $q$INSERT INTO grade (sheet_id, student_id, points)
       SELECT (SELECT sheet_id FROM grade_sheet WHERE sheet_number = 'X-15'), student_id, 40
       FROM student WHERE group_id = pg_temp.grp('ИДБ-23-11') AND status_id = 1$q$,
    $q$UPDATE grade_sheet SET status = 'closed', closed_date = '2026-09-22' WHERE sheet_number = 'X-15'$q$,
    'SELECT TRUE');
SELECT pg_temp.expect_ok('Именная стипендия партнёра доступна и договорнику',
    $q$INSERT INTO order_student (order_id, student_id, reason)
       VALUES (pg_temp.order_of('23-1101', 'scholarship'), pg_temp.st('23-1103'), 'Именная стипендия партнёра')$q$,
    $q$INSERT INTO scholarship (student_id, scholarship_type_id, order_id, start_date, end_date)
       VALUES (pg_temp.st('23-1103'), 4, pg_temp.order_of('23-1101', 'scholarship'), '2026-09-01', '2027-01-31')$q$,
    'SELECT TRUE');
SELECT pg_temp.expect_ok('Старые записи уволенного сотрудника остаются, их можно править',
    'UPDATE study_group SET curator_id = 7 WHERE curator_id = 10',
    'UPDATE employee SET dismissal_date = CURRENT_DATE WHERE employee_id = 10',
    'UPDATE teaching_assignment SET subgroup = 1 WHERE assignment_id = (SELECT MIN(assignment_id) FROM teaching_assignment WHERE teacher_id = 10)',
    'SELECT TRUE');

\echo '== Успеваемость, кандидаты на отчисление, сводка =='
SELECT pg_temp.expect_ok('Отчёт: у зачётной дисциплины в столбце «Экзамен» прочерк',
    $q$SELECT module_1 = '50' AND module_2 = '49' AND credit = '50' AND exam = '—' FROM v_performance
       WHERE student_id = pg_temp.st('23-1101') AND discipline = 'Иностранный язык' AND semester = 4$q$);
SELECT pg_temp.expect_ok('Отчёт: у экзаменационной дисциплины в столбце «Зачёт» прочерк',
    $q$SELECT module_1 = '48' AND module_2 = '51' AND credit = '—' AND exam = '52' AND final_result = 'Отлично'
       FROM v_performance WHERE student_id = pg_temp.st('23-1101') AND discipline = 'Базы данных'$q$);
SELECT pg_temp.expect_ok('Отчёт: у практики нет модулей, но есть оценка в баллах',
    $q$SELECT module_1 = '—' AND module_2 = '—' AND credit = '50' AND final_result = 'Отлично'
       FROM v_performance WHERE student_id = pg_temp.st('23-1101') AND discipline = 'Производственная практика'$q$);
SELECT pg_temp.expect_ok('Отчёт: после пересдачи показывается последний результат',
    $q$SELECT exam = '28' AND final_result = 'Удовлетворительно' FROM v_performance
       WHERE student_id = pg_temp.st('23-1103') AND discipline = 'Базы данных'$q$);
SELECT pg_temp.expect_ok('Отчёт: «н/а» и «неявка»',
    $q$SELECT module_2 = 'н/а' AND exam = 'неявка' FROM v_performance
       WHERE student_id = pg_temp.st('23-1104') AND discipline = 'Проектирование информационных систем'$q$);
SELECT pg_temp.expect_ok('Отчёт: ещё не выставленные баллы — пустые ячейки, а не «н/а»',
    $q$SELECT module_1 IS NULL AND module_2 IS NULL AND exam IS NULL AND credit = '—' FROM v_performance
       WHERE student_id = pg_temp.st('23-1101') AND discipline = 'Системы искусственного интеллекта'$q$);
SELECT pg_temp.expect_ok('Отчёт: после академа результаты прежней группы засчитываются в новой',
    $q$SELECT group_name = 'ИДБ-25-11' AND exam IS NOT NULL AND exam <> '—' FROM v_performance
       WHERE student_id = pg_temp.st('24-1102') AND discipline = 'Математический анализ' AND semester = 1$q$);
SELECT pg_temp.expect_ok('Перевод баллов в оценку: 34 → 3, 35 → 4, 44 → 4, 45 → 5, 54 → 5',
    'SELECT fn_mark(25) = 3 AND fn_mark(34) = 3 AND fn_mark(35) = 4 AND fn_mark(44) = 4 AND fn_mark(45) = 5 AND fn_mark(54) = 5 AND fn_mark(24) IS NULL');
SELECT pg_temp.expect_ok('Кандидат на отчисление: не сдана комиссия',
    $q$SELECT reason = 'Не сдана комиссия' AND max_attempts = 3 FROM v_expulsion_risk WHERE student_id = pg_temp.st('24-1203')$q$);
SELECT pg_temp.expect_ok('Кандидат на отчисление: три задолженности',
    $q$SELECT reason = 'Три и более задолженности' AND debts = 3 FROM v_expulsion_risk WHERE student_id = pg_temp.st('23-1104')$q$);
SELECT pg_temp.expect_ok('Сводка: в данных есть все категории студентов',
    $q$SELECT count(DISTINCT category) >= 8 FROM v_student_summary$q$);
SELECT pg_temp.expect_ok('Сводка: староста, стипендия и категория отличника у Иванова',
    $q$SELECT is_headman AND scholarships LIKE '%Повышенная%' AND category = 'Отличник'
       FROM v_student_summary WHERE record_book_number = '23-1101'$q$);
SELECT pg_temp.expect_error_msg('Модули у практики в учебном плане', 'модулей нет',
    $q$UPDATE curriculum_item SET module_count = 2 WHERE item_id = pg_temp.item('ИДБ-23-11', 'Производственная практика', 6)$q$);

\echo '== Расписание на семестр =='
SELECT pg_temp.expect_ok('Календарь: в праздник 4 ноября занятий нет',
    $q$SELECT NOT EXISTS (SELECT 1 FROM v_schedule_calendar WHERE lesson_date = '2026-11-04')$q$);
SELECT pg_temp.expect_ok('Календарь: занятие по нечётным неделям идёт только в нечётные',
    $q$SELECT count(*) > 0 AND bool_and(week = 'нечётная') FROM v_schedule_calendar
       WHERE slot_id = (SELECT MIN(slot_id) FROM schedule_slot WHERE week_parity = 'odd')$q$);
SELECT pg_temp.expect_ok('Календарь: лекции «Управления программными проектами» только до 25 октября',
    $q$SELECT count(*) > 0 AND bool_and(c.lesson_date <= s.valid_to) FROM v_schedule_calendar c
       JOIN schedule_slot s USING (slot_id) WHERE s.valid_to IS NOT NULL$q$);
SELECT pg_temp.expect_ok('Календарь: у каждой группы занятия весь семестр, с сентября по декабрь',
    $q$SELECT bool_and(first_day <= '2026-09-05' AND last_day >= '2026-12-14') FROM
       (SELECT group_name, MIN(lesson_date) AS first_day, MAX(lesson_date) AS last_day FROM v_schedule_calendar GROUP BY 1) x$q$);
SELECT pg_temp.expect_ok('Сверка часов: расписание покрывает не меньше 85 % плана по каждому занятию',
    'SELECT count(*) > 100 AND bool_and(scheduled_hours >= 0.85 * planned_hours) FROM v_schedule_hours');
SELECT pg_temp.expect_error_msg('Занятие за пределами семестра', 'выходит за даты',
    $q$INSERT INTO schedule_slot (assignment_id, classroom_id, weekday, pair_number, term_id, valid_from, valid_to)
       VALUES (pg_temp.asg('ИДБ-26-11', 'Иностранный язык', 'Практика'), pg_temp.room('Б-301'), 1, 6, 7, '2026-08-01', '2026-09-30')$q$);
SELECT pg_temp.expect_error_msg('Посещаемость в праздничный день', 'праздничный',
    $q$DELETE FROM attendance WHERE slot_id = pg_temp.slot('ИДБ-23-11', 'every')
         AND lesson_date = pg_temp.lesson_day(pg_temp.slot('ИДБ-23-11', 'every'))$q$,
    $q$INSERT INTO holiday (holiday_date, name) VALUES (pg_temp.lesson_day(pg_temp.slot('ИДБ-23-11', 'every')), 'Тестовый выходной')$q$,
    $q$INSERT INTO attendance (slot_id, student_id, lesson_date, is_present)
       SELECT pg_temp.slot('ИДБ-23-11', 'every'), pg_temp.st('23-1101'), MIN(holiday_date), TRUE
       FROM holiday WHERE name = 'Тестовый выходной'$q$);

\echo '== Все проверки пройдены =='
ROLLBACK;
