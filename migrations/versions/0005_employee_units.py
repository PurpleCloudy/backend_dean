"""Keep one employee row while retaining every organizational role."""
from alembic import op

revision = '0005'
down_revision = '0004'


def upgrade():
    op.execute("""
CREATE OR REPLACE VIEW deanery.v_employees WITH(security_invoker=true) AS
SELECT e.employee_id,e.personnel_number,
 concat_ws(' ',p.last_name,p.first_name,p.middle_name) AS full_name,
 pos.title AS position,pos.category,units.unit::varchar AS unit,
 e.employment_rate,e.hire_date,e.dismissal_date,p.phone,p.email
FROM deanery.employee e
JOIN deanery.person p ON p.person_id=e.person_id
JOIN deanery.position pos ON pos.position_id=e.position_id
LEFT JOIN LATERAL (
 SELECT string_agg(label,'; ' ORDER BY role_order,unit_id) AS unit
 FROM (
  SELECT d.short_name AS label,0 AS role_order,d.department_id AS unit_id
  FROM deanery.teacher t JOIN deanery.department d ON d.department_id=t.department_id
  WHERE t.employee_id=e.employee_id
  UNION ALL
  SELECT 'Деканат '||i.short_name,1,i.institute_id
  FROM deanery.dean_office_staff ds JOIN deanery.institute i ON i.institute_id=ds.institute_id
  WHERE ds.employee_id=e.employee_id
  UNION ALL
  SELECT 'Дирекция '||i.short_name,2,i.institute_id
  FROM deanery.institute i WHERE i.director_id=e.employee_id
 ) affiliations
) units ON true;
""")


def downgrade():
    op.execute("""
CREATE OR REPLACE VIEW deanery.v_employees WITH(security_invoker=true) AS
SELECT e.employee_id,e.personnel_number,
 concat_ws(' ',p.last_name,p.first_name,p.middle_name) AS full_name,
 pos.title AS position,pos.category,
 coalesce(d.short_name,'Деканат '||fs.short_name,'Дирекция '||fd.short_name)::varchar AS unit,
 e.employment_rate,e.hire_date,e.dismissal_date,p.phone,p.email
FROM deanery.employee e
JOIN deanery.person p ON p.person_id=e.person_id
JOIN deanery.position pos ON pos.position_id=e.position_id
LEFT JOIN deanery.teacher t ON t.employee_id=e.employee_id
LEFT JOIN deanery.department d ON d.department_id=t.department_id
LEFT JOIN deanery.dean_office_staff ds ON ds.employee_id=e.employee_id
LEFT JOIN deanery.institute fs ON fs.institute_id=ds.institute_id
LEFT JOIN deanery.institute fd ON fd.director_id=e.employee_id;
""")
