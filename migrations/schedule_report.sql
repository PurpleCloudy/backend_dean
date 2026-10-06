
CREATE OR REPLACE VIEW deanery.v_schedule_hours WITH(security_invoker=true) AS
SELECT g.name AS group_name,dis.name AS discipline,lt.name AS lesson_type,
 concat_ws(' ',p.last_name,p.first_name,p.middle_name) AS teacher,
 CASE lt.name WHEN 'Лекция' THEN ci.lecture_hours WHEN 'Практика' THEN ci.practice_hours ELSE ci.lab_hours END AS planned_hours,
 2*coalesce(calendar_counts.n,0) AS scheduled_hours,ta.assignment_id
FROM deanery.teaching_assignment ta
JOIN deanery.study_group g ON g.group_id=ta.group_id
JOIN deanery.curriculum_item ci ON ci.item_id=ta.item_id
JOIN deanery.discipline dis ON dis.discipline_id=ci.discipline_id
JOIN deanery.lesson_type lt ON lt.lesson_type_id=ta.lesson_type_id
JOIN deanery.employee e ON e.employee_id=ta.teacher_id
JOIN deanery.person p ON p.person_id=e.person_id
LEFT JOIN (SELECT ss.assignment_id,count(*) AS n FROM deanery.v_schedule_calendar c JOIN deanery.schedule_slot ss USING(slot_id) GROUP BY ss.assignment_id) calendar_counts ON calendar_counts.assignment_id=ta.assignment_id
WHERE EXISTS(SELECT 1 FROM deanery.schedule_slot s WHERE s.assignment_id=ta.assignment_id)
;
