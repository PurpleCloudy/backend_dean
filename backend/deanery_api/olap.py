"""Interactive relational cubes over the caller's currently visible records."""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr
from psycopg import sql

from .auth import authorise, require_principal
from .db import transaction
from .errors import ApiError
from .resources import manifest

router = APIRouter(prefix='/olap', tags=['OLAP'])
Scalar = StrictStr | StrictInt | StrictFloat | StrictBool | None

CUBES = {
    'students': {
        'label': 'Контингент', 'description': 'Одна запись — один доступный студент. Группа и институт текущие.',
        'sources': ['v_students'], 'key': 'student_id',
        'base': 'SELECT student_id,full_name,group_name,institute,course,status,funding,study_form,enrollment_date FROM deanery.v_students',
        'dimensions': {'institute': ('Институт', 'string'), 'group_name': ('Группа', 'string'),
            'course': ('Курс', 'integer'), 'status': ('Статус', 'string'), 'funding': ('Финансирование', 'string'), 'study_form': ('Форма обучения', 'string')},
        'measures': {'student_count': ('Студенты', 'count(*)', 'Число студентов, соответствующих фильтрам.')},
        'details': {'student_id': ('ID студента', 'integer'), 'full_name': ('Студент', 'string'),
            'enrollment_date': ('Дата зачисления', 'date')},
    },
    'performance': {
        'label': 'Успеваемость', 'description': 'Все записи оценок и попыток, кроме отменённых ведомостей. Это не последние оценки по дисциплине. Группа студента текущая.',
        'sources': ['v_grades'], 'key': 'grade_id',
        'base': "SELECT grade_id,student_id,student,group_name,discipline,semester,stage,sheet_kind,exam_date,to_char(exam_date,'YYYY-MM') AS exam_month,points,passed,result,sheet_number FROM deanery.v_grades",
        'dimensions': {'group_name': ('Группа', 'string'), 'discipline': ('Дисциплина', 'string'),
            'semester': ('Семестр', 'integer'), 'stage': ('Этап', 'string'), 'sheet_kind': ('Вид ведомости', 'string'), 'exam_month': ('Месяц экзамена (ГГГГ-ММ)', 'string')},
        'measures': {'result_count': ('Записи оценок', 'count(*)', 'Все попытки, включая неявки и записи без баллов.'),
            'student_count': ('Уникальные студенты', 'count(DISTINCT student_id)', 'Пересчитывается для всей выборки; суммы групп могут пересекаться.'),
            'average_points': ('Средний балл', 'round(avg(points),2)', 'Только записи с указанными баллами (NULL исключён); итог не является средним средних групп.'),
            'passed_count': ('Записи с оценкой', 'count(*) FILTER (WHERE passed)', 'Число записей с указанными баллами (points IS NOT NULL), все попытки. Это не показатель успешности.'),
            'absence_count': ('Неявки', "count(*) FILTER (WHERE result='Неявка')", 'Записи с признаком неявки.')},
        'details': {'grade_id': ('ID оценки', 'integer'), 'student_id': ('ID студента', 'integer'), 'student': ('Студент', 'string'),
            'exam_date': ('Дата экзамена', 'date'), 'points': ('Баллы', 'integer'), 'passed': ('Баллы указаны', 'boolean'),
            'result': ('Результат', 'string'), 'sheet_number': ('Ведомость', 'string')},
    },
    'attendance': {
        'label': 'Посещаемость', 'description': 'Одна запись — отметка студента на занятии. Процент считается только по зарегистрированным отметкам, а не всему расписанию.',
        'sources': ['attendance', 'student', 'study_group', 'schedule_slot', 'teaching_assignment', 'curriculum_item', 'discipline'], 'key': 'attendance_id',
        'base': """SELECT a.attendance_id,a.student_id,g.name AS group_name,d.name AS discipline,
            a.lesson_date,to_char(a.lesson_date,'YYYY-MM') AS lesson_month,a.is_present
            FROM deanery.attendance a JOIN deanery.student s ON s.student_id=a.student_id
            JOIN deanery.study_group g ON g.group_id=s.group_id
            JOIN deanery.schedule_slot ss ON ss.slot_id=a.slot_id
            JOIN deanery.teaching_assignment ta ON ta.assignment_id=ss.assignment_id
            JOIN deanery.curriculum_item ci ON ci.item_id=ta.item_id
            JOIN deanery.discipline d ON d.discipline_id=ci.discipline_id""",
        'dimensions': {'group_name': ('Текущая группа', 'string'), 'discipline': ('Дисциплина', 'string'),
            'lesson_month': ('Месяц занятия (ГГГГ-ММ)', 'string'), 'is_present': ('Присутствие', 'boolean')},
        'measures': {'record_count': ('Отметки', 'count(*)', 'Число зарегистрированных отметок.'),
            'student_count': ('Уникальные студенты', 'count(DISTINCT student_id)', 'Различные студенты в выборке.'),
            'present_count': ('Присутствия', 'count(*) FILTER (WHERE is_present)', 'Отметки о присутствии.'),
            'absent_count': ('Пропуски', 'count(*) FILTER (WHERE NOT is_present)', 'Отметки об отсутствии.'),
            'attendance_percent': ('Посещаемость, %', 'round(100.0*count(*) FILTER (WHERE is_present)/nullif(count(*),0),2)', 'Присутствия / все зарегистрированные отметки. При отсутствии записей — null.')},
        'details': {'attendance_id': ('ID отметки', 'integer'), 'student_id': ('ID студента', 'integer'), 'lesson_date': ('Дата занятия', 'date')},
    },
}


class Filter(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    field: str = Field(max_length=80)
    op: Literal['eq', 'ne', 'in', 'gte', 'lte'] = 'eq'
    value: Scalar | list[Scalar]


class DrillRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cube: Literal['students', 'performance', 'attendance']
    filters: list[Filter] = Field(default_factory=list, max_length=12)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=100000)


class CubeRequest(DrillRequest):
    dimensions: list[str] = Field(default_factory=list, max_length=3)
    measures: list[str] = Field(min_length=1, max_length=5)


class FieldInfo(BaseModel):
    id: str
    label: str
    type: Literal['string', 'integer', 'boolean', 'date']


class MeasureInfo(BaseModel):
    id: str
    label: str
    description: str


class CubeInfo(BaseModel):
    id: str
    label: str
    description: str
    dimensions: list[FieldInfo]
    measures: list[MeasureInfo]
    detail_fields: list[FieldInfo]


class Catalog(BaseModel):
    cubes: list[CubeInfo]


class DrillResponse(BaseModel):
    cube: str
    items: list[dict[str, Scalar]]
    total_rows: int
    limit: int
    offset: int
    has_more: bool


class CubeResponse(BaseModel):
    cube: str
    dimensions: list[str]
    measures: list[str]
    items: list[dict[str, Scalar]]
    totals: dict[str, int | float | None]
    total_groups: int
    limit: int
    offset: int
    has_more: bool


def fields(cube):
    return {**cube['dimensions'], **cube['details']}


def cube_visible(cube, principal):
    return all(principal.roles.intersection(manifest['resources'][name]['read_roles']) for name in cube['sources'])


def filters_sql(cube, filters):
    predicates, parameters = [], []
    for item in filters:
        if item.field not in cube['dimensions']:
            raise ApiError(422, 'invalid_olap_filter', 'Фильтр должен быть измерением выбранного куба')
        kind = cube['dimensions'][item.field][1]
        values = item.value if isinstance(item.value, list) else [item.value]
        if (item.op == 'in') != isinstance(item.value, list) or not 1 <= len(values) <= 100:
            raise ApiError(422, 'invalid_olap_filter', 'Оператор in требует список из 1–100 значений')
        for value in values:
            valid = value is None or (kind == 'integer' and type(value) is int) or (kind == 'boolean' and type(value) is bool) or (kind == 'string' and isinstance(value, str) and len(value) <= 255)
            if not valid or (item.op in ('gte', 'lte') and (value is None or kind == 'boolean')):
                raise ApiError(422, 'invalid_olap_filter', 'Неверный тип значения фильтра')
        column = sql.Identifier(item.field)
        if item.op == 'in':
            nonnull = [v for v in values if v is not None]
            parts = []
            if nonnull:
                parts.append(sql.SQL('{} = ANY(%s)').format(column)); parameters.append(nonnull)
            if None in values:
                parts.append(sql.SQL('{} IS NULL').format(column))
            predicates.append(sql.SQL('(') + sql.SQL(' OR ').join(parts) + sql.SQL(')'))
        elif item.value is None:
            predicates.append(sql.SQL('{} IS '+('NOT ' if item.op == 'ne' else '')+'NULL').format(column))
        else:
            op = {'eq': '=', 'ne': '<>', 'gte': '>=', 'lte': '<='}[item.op]
            predicates.append(sql.SQL('{} '+op+' %s').format(column)); parameters.append(item.value)
    return (sql.SQL(' WHERE ') + sql.SQL(' AND ').join(predicates) if predicates else sql.SQL('')), parameters


def query_sql(body, drill=False):
    cube = CUBES[body.cube]
    where, parameters = filters_sql(cube, body.filters)
    prefix = sql.SQL('WITH base AS ({}), filtered AS (SELECT * FROM base{}) ').format(sql.SQL(cube['base']), where)
    if drill:
        query = prefix + sql.SQL("SELECT (SELECT count(*) FROM filtered) AS total_rows,coalesce((SELECT jsonb_agg(to_jsonb(p)) FROM (SELECT {} FROM filtered ORDER BY {} LIMIT %s OFFSET %s) p),'[]'::jsonb) AS items").format(sql.SQL(',').join(map(sql.Identifier, fields(cube))), sql.Identifier(cube['key']))
    else:
        if len(set(body.dimensions)) != len(body.dimensions) or len(set(body.measures)) != len(body.measures) or not set(body.dimensions) <= cube['dimensions'].keys() or not set(body.measures) <= cube['measures'].keys():
            raise ApiError(422, 'invalid_olap_selection', 'Выберите разные измерения и показатели из каталога')
        dims = [sql.Identifier(d) for d in body.dimensions]
        measures = [sql.SQL('{} AS {}').format(sql.SQL(cube['measures'][m][1]), sql.Identifier(m)) for m in body.measures]
        totals = sql.SQL(',').join(measures)
        grouped = sql.SQL('SELECT {} FROM filtered{}').format(sql.SQL(',').join(dims+measures), sql.SQL(' GROUP BY ')+sql.SQL(',').join(dims) if dims else sql.SQL(''))
        order = sql.SQL(' ORDER BY ')+sql.SQL(',').join(sql.SQL('{} NULLS LAST').format(d) for d in dims) if dims else sql.SQL('')
        query = prefix + sql.SQL(", groups AS ({}) SELECT (SELECT count(*) FROM groups) AS total_groups,(SELECT to_jsonb(t) FROM (SELECT {} FROM filtered) t) AS totals,coalesce((SELECT jsonb_agg(to_jsonb(p)) FROM (SELECT * FROM groups{} LIMIT %s OFFSET %s) p),'[]'::jsonb) AS items").format(grouped, totals, order)
    return query, [*parameters, body.limit, body.offset]


@router.get('/catalog', response_model=Catalog)
async def catalog(principal=Depends(require_principal)):
    return {'cubes': [{'id': name, 'label': cube['label'], 'description': cube['description'],
        'dimensions': [{'id': k, 'label': v[0], 'type': v[1]} for k, v in cube['dimensions'].items()],
        'measures': [{'id': k, 'label': v[0], 'description': v[2]} for k, v in cube['measures'].items()],
        'detail_fields': [{'id': k, 'label': v[0], 'type': v[1]} for k, v in fields(cube).items()]}
        for name, cube in CUBES.items() if cube_visible(cube, principal)]}


async def execute(body, request, principal, drill=False):
    query, parameters = query_sql(body, drill)
    async with transaction(principal, request.state.request_id, readonly=True) as conn:
        for source in CUBES[body.cube]['sources']:
            await authorise(conn, principal, 'read', source)
        row = await (await conn.execute(query, parameters)).fetchone()
    count = row['total_rows' if drill else 'total_groups']
    return {'cube': body.cube, **row, 'limit': body.limit, 'offset': body.offset,
        'has_more': body.offset+body.limit < count, **({} if drill else {'dimensions': body.dimensions, 'measures': body.measures})}


@router.post('/query', response_model=CubeResponse)
async def query_cube(body: CubeRequest, request: Request, principal=Depends(require_principal)):
    return await execute(body, request, principal)


@router.post('/drill', response_model=DrillResponse)
async def drill_cube(body: DrillRequest, request: Request, principal=Depends(require_principal)):
    return await execute(body, request, principal, True)
