"""Scoped tools. SQL shape validation adapts dean-agent's MIT-licensed pglast parser."""
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4
from typing import Annotated, Literal, Union

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, create_model
from pglast import ast, parse_sql
from pglast.stream import RawStream
from psycopg import sql as pgsql
from psycopg.types.json import Jsonb

from .auth import require_principal, authorise, load_principal
from .db import transaction
from .errors import ApiError
from .resources import manifest, mutate_resource, read_models
from .schemas import json_value
from .workflows import SavedGrade
from . import storage

router = APIRouter(tags=['agent proposals'])
FUNCTIONS = {'count', 'sum', 'avg', 'min', 'max', 'lower', 'upper', 'length', 'char_length',
             'round', 'abs', 'concat', 'concat_ws', 'date_part', 'date_trunc', 'age',
             'row_number', 'rank', 'dense_rank', 'nullif', 'coalesce', 'greatest', 'least'}
OPERATORS = {'=', '<>', '!=', '<', '>', '<=', '>=', '+', '-', '*', '/', '%', '||', '~~', '!~~', '~~*', '!~~*'}
TYPES = {'text', 'varchar', 'bpchar', 'int2', 'int4', 'int8', 'numeric', 'float4', 'float8',
         'bool', 'date', 'timestamp', 'timestamptz', 'interval'}
NODES = {'SelectStmt', 'ResTarget', 'ColumnRef', 'String', 'Integer', 'Float', 'Boolean', 'A_Const',
         'RangeVar', 'RangeSubselect', 'JoinExpr', 'Alias', 'A_Expr', 'BoolExpr', 'NullTest',
         'BooleanTest', 'FuncCall', 'SortBy', 'TypeCast', 'TypeName', 'CaseExpr', 'CaseWhen',
         'CoalesceExpr', 'MinMaxExpr', 'SubLink', 'WithClause', 'CommonTableExpr', 'WindowDef',
         'SQLValueFunction', 'RowExpr', 'A_ArrayExpr'}
PROPOSAL_TABLES = {'document_request', 'academic_work', 'grade', 'attendance', 'practice_placement',
                   'teaching_assignment', 'schedule_slot', 'organization_contact', 'contact_person', 'grade_sheet'}
SHEET_INSERT = {'sheet_number', 'item_id', 'group_id', 'examiner_id', 'stage', 'sheet_kind',
                'issue_date', 'exam_date', 'closed_date', 'status'}
SHEET_UPDATE = {'examiner_id', 'exam_date', 'closed_date', 'status'}


_previews = []
for _table in sorted(PROPOSAL_TABLES):
    _spec = manifest['resources'][_table]
    _full = read_models[_table]
    _after = SavedGrade if _table == 'grade' else _full
    _insert = create_model('ProposalInsert_' + _table, __config__=ConfigDict(extra='forbid'),
        **{name: (field.annotation, ...) for name, field in _after.model_fields.items() if name not in _spec['key']})
    _previews.append(create_model('ProposalPreview_' + _table, __config__=ConfigDict(extra='forbid'),
        operation=(Literal['INSERT', 'UPDATE'], ...), table=(Literal[_table], ...),
        affected_rows=(Literal[1], ...), before=(_full | None, ...), after=(_after | _insert, ...),
        changed_columns=(list[str], ...), snapshot_hash=(str | None, ...), related_effects=(list[str], ...),
        note=(str | None, ...), applied=(_after | None, None)))
ProposalPreview = Annotated[Union[tuple(_previews)], Field(discriminator='table')]


class ProposalSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    preview: ProposalPreview
    explanation: str
    reason: str


class ProposalResponse(ProposalSummary):
    status: Literal['pending', 'approved', 'rejected']
    sql_text: str
    decided_by: str | None
    decision_reason: str | None


class ProposalDecisionResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    status: Literal['approved', 'rejected']
    preview: ProposalPreview


def walk(node):
    pending = [(node, 0)]
    count = 0
    while pending:
        current, depth = pending.pop()
        if depth > 64 or count > 2000:
            raise rejected('Слишком сложный SQL')
        if isinstance(current, ast.Node):
            count += 1
            yield current
            pending.extend((getattr(current, field), depth+1) for field in current.__slots__)
        elif isinstance(current, (tuple, list)):
            pending.extend((child, depth+1) for child in current)


def rejected(message='Запрос содержит недопустимую конструкцию'):
    return ApiError(422, 'query_rejected', message)


def literal(value):
    if not isinstance(value, ast.A_Const):
        raise rejected('Разрешены только простые значения')
    if value.isnull:
        return None
    if isinstance(value.val, ast.String):
        return value.val.sval
    if isinstance(value.val, ast.Integer):
        return value.val.ival
    if isinstance(value.val, ast.Float):
        return value.val.fval  # Decimal remains exact through core's typed validation.
    if isinstance(value.val, ast.Boolean):
        return value.val.boolval
    raise rejected()


def sanitized_metadata(principal):
    rows = []
    for name, spec in manifest['resources'].items():
        if principal.roles.intersection(spec['read_roles']):
            for position, field in enumerate(agent_fields(spec), 1):
                rows.append(('deanery', name, field, spec['fields'][field]['type'],
                             'YES' if spec['fields'][field]['nullable'] else 'NO', position))
    if not rows:
        return "SELECT ''::text AS table_schema,''::text AS table_name,''::text AS column_name,''::text AS data_type,''::text AS is_nullable,0 AS ordinal_position WHERE false"
    values = ','.join('(' + ','.join(pgsql.Literal(v).as_string() for v in row) + ')' for row in rows)
    return 'SELECT table_schema,table_name,column_name,data_type,is_nullable,ordinal_position FROM (VALUES ' + values + ') AS m(table_schema,table_name,column_name,data_type,is_nullable,ordinal_position)'


def agent_fields(spec):
    """Audit metadata is useful to the agent; free audit payloads stay human-only."""
    return spec.get('agent_read_fields', spec['read_fields'])


def validate_read_query(text, principal):
    if len(text) > 6000:
        raise rejected('SQL слишком длинный')
    try:
        statements = parse_sql(text)
    except Exception as exc:
        raise rejected('Ошибка синтаксиса SQL') from exc
    if len(statements) != 1 or not isinstance(statements[0].stmt, ast.SelectStmt):
        raise rejected('Разрешён один SELECT')
    root = statements[0].stmt
    if not isinstance(root.limitCount, ast.A_Const) or not isinstance(root.limitCount.val, ast.Integer) or not 1 <= root.limitCount.val.ival <= 60:
        raise rejected('Укажите LIMIT от 1 до 60')
    nodes = list(walk(root))
    ctes = set()
    resources = set()
    allowed_columns = set()
    for node in nodes:
        if type(node).__name__ not in NODES:
            raise rejected()
        if isinstance(node, ast.SelectStmt) and (node.intoClause is not None or node.lockingClause):
            raise rejected()
        if isinstance(node, (ast.WithClause, ast.CommonTableExpr)):
            raise rejected('WITH недоступен; используйте вложенный SELECT')
        if isinstance(node, ast.RangeVar):
            if node.catalogname or node.schemaname not in (None, 'deanery', 'information_schema'):
                raise rejected('Схема недоступна')
            if node.relname in ctes and node.schemaname is None:
                continue
            if node.schemaname == 'information_schema' and node.relname == 'columns':
                allowed_columns.update({'table_schema', 'table_name', 'column_name', 'data_type', 'is_nullable', 'ordinal_position'})
            else:
                spec = manifest['resources'].get(node.relname)
                if node.schemaname == 'information_schema' or not spec or not principal.roles.intersection(spec['read_roles']) or not agent_fields(spec):
                    raise rejected('Отношение недоступно')
                resources.add(node.relname)
                allowed_columns.update(agent_fields(spec))
        if isinstance(node, ast.FuncCall):
            parts = [part.sval for part in node.funcname]
            if len(parts) not in (1, 2) or len(parts) == 2 and parts[0] != 'pg_catalog' or parts[-1] not in FUNCTIONS:
                raise rejected('Функция недоступна')
            node.funcname = (ast.String(sval='pg_catalog'), ast.String(sval=parts[-1]))
        if isinstance(node, ast.A_Expr) and (len(node.name or ()) != 1 or node.name[0].sval not in OPERATORS):
            raise rejected('Оператор недоступен')
        if isinstance(node, ast.TypeName):
            names = [part.sval for part in node.names or ()]
            if not names or len(names) > 2 or len(names) == 2 and names[0] != 'pg_catalog' or names[-1] not in TYPES or node.arrayBounds:
                raise rejected('Тип недоступен')
        if isinstance(node, ast.SQLValueFunction) and 'CURRENT_DATE' not in str(node.op.name) and 'CURRENT_TIMESTAMP' not in str(node.op.name):
            raise rejected('Служебные значения недоступны')
    allowed_columns.update(node.name for node in nodes if isinstance(node, ast.ResTarget) and node.name)
    allowed_columns.update(c.sval for node in nodes if isinstance(node, (ast.Alias, ast.CommonTableExpr)) for c in (getattr(node, 'colnames', None) or getattr(node, 'aliascolnames', None) or ()))
    for node in nodes:
        if isinstance(node, ast.ColumnRef):
            parts = node.fields
            if not 1 <= len(parts) <= 2 or not all(isinstance(part, ast.String) for part in parts) or parts[-1].sval not in allowed_columns:
                raise rejected('Столбец недоступен; перечислите разрешённые столбцы')
    # Replace relations, including nested subqueries, with explicit field projections.
    def project(node):
        if isinstance(node, ast.RangeVar) and not (node.relname in ctes and node.schemaname is None):
            if node.schemaname == 'information_schema':
                query = sanitized_metadata(principal)
            else:
                spec = manifest['resources'][node.relname]
                query = pgsql.SQL('SELECT {} FROM deanery.{}').format(
                    pgsql.SQL(',').join(map(pgsql.Identifier, agent_fields(spec))), pgsql.Identifier(node.relname)).as_string()
            return ast.RangeSubselect(subquery=parse_sql(query)[0].stmt,
                alias=node.alias or ast.Alias(aliasname=node.relname))
        if isinstance(node, ast.Node):
            for field in node.__slots__:
                value = getattr(node, field)
                if isinstance(value, ast.Node):
                    setattr(node, field, project(value))
                elif isinstance(value, tuple):
                    setattr(node, field, tuple(project(v) for v in value))
        return node
    return RawStream()(project(root)), root.limitCount.val.ival, resources


async def query_deanery(conn, principal, sql):
    query, limit, resources = validate_read_query(sql, principal)
    for name in resources:
        await authorise(conn, principal, 'read', name)
    await conn.execute('SET LOCAL transaction_read_only = on')
    await conn.execute('SET LOCAL ROLE deanery_reader')
    try:
        await conn.execute("SET LOCAL search_path TO pg_catalog")
        await conn.execute("SET LOCAL statement_timeout='5s'")
        cursor = await conn.execute(query)
        rows = await cursor.fetchmany(limit)
        return {'columns': [column.name for column in cursor.description], 'rows': json_value(rows), 'returned': len(rows), 'limit': limit}
    finally:
        await conn.execute('SET LOCAL ROLE NONE')


def parse_change(text):
    if len(text) > 4000:
        raise rejected('SQL слишком длинный')
    try:
        statements = parse_sql(text)
    except Exception as exc:
        raise rejected('Ошибка синтаксиса SQL') from exc
    if len(statements) != 1 or not isinstance(statements[0].stmt, (ast.InsertStmt, ast.UpdateStmt)):
        raise rejected('Нужен один INSERT или UPDATE')
    node = statements[0].stmt
    relation = node.relation
    if relation.schemaname not in (None, 'deanery') or relation.catalogname or relation.alias or relation.relname not in PROPOSAL_TABLES:
        raise rejected('Изменение требует предметной команды или недоступно')
    spec = manifest['resources'][relation.relname]
    if node.withClause or node.returningClause or len(spec['key']) != 1:
        raise rejected()
    key = None
    if isinstance(node, ast.InsertStmt):
        source = node.selectStmt
        if node.onConflictClause or not isinstance(source, ast.SelectStmt) or not source.valuesLists or len(source.valuesLists) != 1:
            raise rejected('Нужна одна строка VALUES')
        if any(getattr(source, name) for name in ('targetList', 'fromClause', 'whereClause', 'sortClause', 'limitCount',
                                                'limitOffset', 'withClause', 'lockingClause', 'distinctClause', 'groupClause', 'havingClause')):
            raise rejected('INSERT допускает только одну строку VALUES')
        columns = [item.name for item in node.cols or ()]
        values = [literal(item) for item in source.valuesLists[0]]
        operation = 'create'
    else:
        if node.fromClause or any(item.indirection for item in node.targetList):
            raise rejected()
        columns = [item.name for item in node.targetList]
        values = [literal(item.val) for item in node.targetList]
        where = node.whereClause
        if not isinstance(where, ast.A_Expr) or len(where.name) != 1 or where.name[0].sval != '=' or not isinstance(where.lexpr, ast.ColumnRef) or len(where.lexpr.fields) != 1 or not isinstance(where.lexpr.fields[0], ast.String) or where.lexpr.fields[0].sval != spec['key'][0]:
            raise rejected('UPDATE должен выбирать один первичный ключ')
        key = literal(where.rexpr)
        if type(key) is not int or key < 1:
            raise rejected('Неверный первичный ключ')
        operation = 'update'
    permitted = (SHEET_INSERT if operation == 'create' else SHEET_UPDATE) if relation.relname == 'grade_sheet' else ({'sheet_id', 'student_id', 'points', 'is_absent'} if operation == 'create' else {'points', 'is_absent'}) if relation.relname == 'grade' else set(spec['write_fields'])
    if not columns or len(columns) != len(values) or len(set(columns)) != len(columns) or set(columns) - permitted:
        raise rejected('Столбцы недоступны для изменения')
    change = {'sql': RawStream()(node), 'table': relation.relname, 'operation': operation, 'key': key,
              'values': dict(zip(columns, values)), 'pk': spec['key'][0]}
    if change['table'] == 'grade_sheet': sheet_commands(change)
    return change


def sheet_commands(change):
    """Preserve SQL omission/NULL semantics while using protected commands."""
    values = dict(change['values'])
    status = values.pop('status', 'open')
    closed = values.pop('closed_date', None)
    if change['operation'] == 'create':
        if status != 'open' or closed is not None or 'issue_date' not in values:
            raise rejected('Новая ведомость должна быть открыта и иметь дату выдачи')
        return [('create_grade_sheet', values)]
    if status not in ('open', 'closed', 'cancelled') or status != 'closed' and closed is not None:
        raise rejected('Недопустимое состояние или дата закрытия ведомости')
    if status == 'closed' and closed is None:
        raise rejected('Для закрытия SQL должен явно указывать closed_date')
    if 'examiner_id' in values and values['examiner_id'] is None:
        raise rejected('Экзаменатор обязателен')
    commands = [('update_grade_sheet', {'sheet_id': change['key'], **values})] if values else []
    if status == 'closed':
        commands.append(('close_grade_sheet', {'sheet_id': change['key'], 'closed_date': closed}))
    elif status == 'cancelled':
        commands.append(('cancel_grade_sheet', {'sheet_id': change['key']}))
    return commands


async def current_row(conn, principal, change):
    if change['key'] is None:
        return None, None
    await authorise(conn, principal, 'workflow' if change['table'] in ('grade', 'grade_sheet') else 'update', change['table'], change['key'])
    row = await (await conn.execute(pgsql.SQL('SELECT to_jsonb(t) AS value FROM deanery.{} t WHERE {}=%s FOR UPDATE').format(
        pgsql.Identifier(change['table']), pgsql.Identifier(change['pk'])), (change['key'],))).fetchone()
    if not row:
        raise ApiError(404, 'not_found', 'Запись не найдена')
    full = row['value']
    snapshot = hashlib.sha256(json.dumps(full, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
    allowed = manifest['resources'][change['table']]['read_fields']
    return {key: value for key, value in full.items() if key in allowed}, snapshot


async def apply_change(conn, principal, change, proposal_id):
    if change['table'] == 'grade_sheet':
        from .workflows import run_workflow
        commands = sheet_commands(change)
        sheet_id = change['key']
        if sheet_id is not None:
            before, _ = await current_row(conn, principal, change)
            if before['status'] != 'open':
                raise ApiError(409, 'invalid_state', 'Закрытая или отменённая ведомость неизменяема')
        # The common preview/decision transaction encloses all commands. A failed
        # transition rolls back metadata, command results and audit together.
        for name, values in commands:
            result = await run_workflow(conn, principal, name, values, 'proposal:' + str(proposal_id) + ':' + name)
            sheet_id = result['sheet_id']
        after, _ = await current_row(conn, principal, {**change, 'key': sheet_id})
        return after
    if change['table'] != 'grade':
        return await mutate_resource(conn, principal, change['table'], change['operation'], change['key'], change['values'])
    from .workflows import run_workflow
    values = dict(change['values'])
    if change['key'] is not None:
        before, _ = await current_row(conn, principal, change)
        values = {key: before[key] for key in ('sheet_id', 'student_id', 'points', 'is_absent')} | values
    elif 'sheet_id' in values and 'student_id' in values:
        exists = await (await conn.execute('SELECT 1 FROM deanery.grade WHERE sheet_id=%s AND student_id=%s',
            (values['sheet_id'], values['student_id']))).fetchone()
        if exists:
            raise ApiError(409, 'grade_exists', 'Оценка уже существует; требуется предложение UPDATE')
    return await run_workflow(conn, principal, 'record_grade', values, 'proposal:' + str(proposal_id))


async def create_proposal(conn, principal, sql, explanation, reason, session_id, run_id):
    if not 15 <= len(explanation.strip()) <= 2000 or not 10 <= len(reason.strip()) <= 2000:
        raise rejected('Укажите объяснение и причину изменения')
    change = parse_change(sql)
    before, snapshot = await current_row(conn, principal, change)
    if change['table'] == 'grade' and change['operation'] == 'update':
        target = await (await conn.execute(
            'SELECT gs.status,gs.sheet_number,s.record_book_number FROM deanery.grade_sheet gs '
            'JOIN deanery.student s ON s.student_id=%s WHERE gs.sheet_id=%s FOR UPDATE OF gs',
            (before['student_id'], before['sheet_id']))).fetchone()
        if target and target['status'] == 'closed':
            from .workflows import run_workflow
            # A closed grade has its own native four-eyes lifecycle. Do not also
            # create a generic SQL proposal or claim the grade was changed.
            values = {key: before[key] for key in ('points', 'is_absent')} | change['values']
            await conn.execute('SELECT backend.set_audit_run(%s)', (str(run_id),))
            return await run_workflow(conn, principal, 'request_grade_correction', {
                'sheet_number': target['sheet_number'], 'record_book': target['record_book_number'],
                'new_points': values['points'], 'new_is_absent': values['is_absent'], 'reason': reason.strip(),
            }, 'agent-correction:' + str(run_id) + ':' + hashlib.sha256(change['sql'].encode()).hexdigest()[:32])
    proposal_id = uuid4()
    after = await preview_change(conn, principal, change, proposal_id)
    after = json_value(after)
    if change['operation'] == 'update' and before == after:
        raise ApiError(409, 'no_change', 'Предложение ничего не меняет')
    if change['operation'] == 'create':
        after.pop(change['pk'], None)
    preview = {'operation': 'INSERT' if change['operation'] == 'create' else 'UPDATE',
        'table': change['table'], 'affected_rows': 1, 'before': before, 'after': after,
        'changed_columns': list(change['values']), 'snapshot_hash': snapshot,
        'related_effects': ['Аудит при подтверждении'], 'note': 'Новый ID присваивается при подтверждении' if before is None else None}
    await conn.execute('INSERT INTO public.sql_change_proposals(id,actor_id,agent_run_id,sql_text,explanation,reason,preview,owner_id,session_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (proposal_id, f'user:{principal.user_id}', str(run_id), change['sql'], explanation.strip(), reason.strip(), Jsonb(preview), principal.user_id, session_id))
    return {'proposal_id': str(proposal_id), 'confirmation_required': True, 'preview': preview,
            'explanation': explanation.strip(), 'reason': reason.strip()}


async def preview_change(conn, principal, change, proposal_id):
    # The savepoint restores every protected context field as well as all domain,
    # command and audit effects, including when a callback raises.
    async with conn.transaction(force_rollback=True):
        await conn.execute('SELECT backend.set_actor(%s,%s)', (principal.user_id, 'proposal-preview:' + str(proposal_id)))
        principal = await load_principal(conn, principal.user_id)
        await authorise(conn, principal, 'workflow' if change['table'] in ('grade', 'grade_sheet') else change['operation'],
                        change['table'], change['key'])
        result = await apply_change(conn, principal, change, proposal_id)
        await conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
        return result


async def proposal_row(conn, principal, proposal_id, lock=False):
    row = await (await conn.execute('SELECT * FROM public.sql_change_proposals WHERE id=%s' + (' FOR UPDATE' if lock else ''), (proposal_id,))).fetchone()
    if row is None or row['owner_id'] != principal.user_id and not principal.roles & {'admin', 'director', 'dean_staff'}:
        raise ApiError(404, 'proposal_not_found', 'Предложение не найдено')
    change = parse_change(row['sql_text'])
    try:
        initiator = await load_principal(conn, row['owner_id'])
        for actor in {initiator.user_id: initiator, principal.user_id: principal}.values():
            async with conn.transaction(force_rollback=True):
                await conn.execute('SELECT backend.set_actor(%s,%s)', (actor.user_id, 'proposal-review:' + str(proposal_id)))
                actor = await load_principal(conn, actor.user_id)
                # Review/rejection requires current rights and subject scope,
                # not continued validity of the proposed domain transition.
                key = change['key']
                if key is None and row['status'] == 'approved':
                    key = row['preview']['applied'][change['pk']]
                await authorise(conn, actor, 'workflow' if change['table'] in ('grade', 'grade_sheet') else change['operation'], change['table'], key)
                subject = row['preview']['after']
                if key is not None:
                    record = await (await conn.execute(pgsql.SQL('SELECT to_jsonb(t) AS value FROM deanery.{} t WHERE {}=%s FOR UPDATE').format(
                        pgsql.Identifier(change['table']), pgsql.Identifier(change['pk'])), (key,))).fetchone()
                    if record is None:
                        raise ApiError(404, 'not_found', 'Запись не найдена')
                    subject = record['value']
                for value in (subject, {**subject, **change['values']}):
                    allowed = await (await conn.execute('SELECT backend.allowed(%s,%s,true) AS ok',
                        (change['table'], Jsonb(json_value(value))))).fetchone()
                    if not allowed['ok']:
                        raise ApiError(404, 'proposal_not_found', 'Предложение не найдено')
    except ApiError as exc:
        if row['owner_id'] != principal.user_id or exc.status in (401, 403, 404):
            raise ApiError(404, 'proposal_not_found', 'Предложение не найдено') from None
        raise
    return row, change


async def decide_proposal(conn, principal, proposal_id, approve, reason, idempotency_key, request_id=None):
    if not 10 <= len(reason.strip()) <= 2000 or not 1 <= len(idempotency_key) <= 128:
        raise ApiError(422, 'invalid_decision', 'Укажите причину решения и ключ идемпотентности')
    principal = await load_principal(conn, principal.user_id)
    row, change = await proposal_row(conn, principal, proposal_id, True)
    if not principal.roles & {'admin', 'director', 'dean_staff'}:
        raise ApiError(403, 'forbidden', 'Подтверждает уполномоченный сотрудник')
    if row['status'] != 'pending':
        if row['decided_by'] == f'user:{principal.user_id}' and row['decision_key'] == idempotency_key and row['decision_approve'] == approve and row['decision_reason'] == reason.strip():
            return {'id': row['id'], 'status': row['status'], 'preview': row['preview']}
        raise ApiError(409, 'proposal_decided', 'Решение уже принято')
    from datetime import datetime, timezone
    if row['expires_at'] <= datetime.now(timezone.utc):
        raise ApiError(409, 'proposal_expired', 'Срок подтверждения истёк')
    await conn.execute('SELECT backend.set_proposal_audit(%s)', (proposal_id,))
    preview = row['preview']
    if approve:
        _, current = await current_row(conn, principal, change)
        if current != preview['snapshot_hash']:
            raise ApiError(409, 'stale_proposal', 'Запись изменилась после предпросмотра')
        initiator = await load_principal(conn, row['owner_id'])
        for actor in {initiator.user_id: initiator, principal.user_id: principal}.values():
            await preview_change(conn, actor, change, proposal_id)
        applied = await apply_change(conn, principal, change, proposal_id)
        preview = {**preview, 'applied': json_value(applied)}
    status = 'approved' if approve else 'rejected'
    await conn.execute('UPDATE public.sql_change_proposals SET status=%s,preview=%s,decided_by=%s,decision_reason=%s,resolved_at=now(),decision_key=%s,decision_approve=%s WHERE id=%s',
        (status, Jsonb(preview), f'user:{principal.user_id}', reason.strip(), idempotency_key, approve, proposal_id))
    await conn.execute("INSERT INTO backend.events(user_id,request_id,action,resource,record_key) "
        "VALUES(%s,%s,'proposal_decision','sql_change_proposals',%s)",
        (principal.user_id, request_id, Jsonb({'proposal_id': str(proposal_id), 'initiator_id': row['owner_id'],
                                 'approver_id': principal.user_id, 'decision': status})))
    return {'id': proposal_id, 'status': status, 'preview': preview}


async def search_regulations(conn, principal, query, limit=5):
    if not 3 <= len(query.strip()) <= 300 or not 1 <= limit <= 20:
        raise rejected('Укажите поисковый запрос длиной от 3 до 300 символов')
    vector = (await storage.embed([query]))[0]
    allowed = [{'key': 'owner_id', 'match': {'value': principal.user_id}},
               {'key': 'associated', 'match': {'value': True}},
               {'is_null': {'key': 'institute_id'}}]
    if principal.institute_ids:
        allowed.append({'key': 'institute_id', 'match': {'any': list(principal.institute_ids)}})
    vector_filter = {'must': [{'key': 'purpose', 'match': {'value': 'regulation'}}]}
    if 'admin' not in principal.roles:
        vector_filter['should'] = allowed
    prefetch = [{'query': vector['dense'], 'using': 'dense', 'limit': 60, 'filter': vector_filter}]
    if vector['sparse']['indices']:
        prefetch.append({'query': vector['sparse'], 'using': 'sparse', 'limit': 60, 'filter': vector_filter})
    value = await storage.qdrant('POST', '/points/query', json={'prefetch': prefetch, 'query': {'fusion': 'rrf'}, 'limit': 60})
    output = []
    from .files import get_file
    for point in value['result']['points']:
        try:
            point_id = UUID(str(point['id']))
        except (ValueError, KeyError):
            continue
        row = await (await conn.execute("SELECT c.*,v.file_id,v.quality,f.title,f.source FROM backend.file_chunks c "
            "JOIN backend.file_versions v ON v.id=c.version_id JOIN backend.files f ON f.id=v.file_id "
            "WHERE c.id=%s AND v.state='ready' AND f.active_version_id=v.id AND f.purpose='regulation'", (point_id,))).fetchone()
        if row is None:
            continue
        try:
            await get_file(conn, principal, row['file_id'])
        except ApiError as exc:
            if exc.status in (403, 404):
                continue
            raise
        output.append({'file_id': str(row['file_id']), 'version_id': str(row['version_id']),
            'title': row['title'], 'source': row['source'], 'page': row['page'], 'excerpt': row['content'][:1800],
            'quality': row['quality'], 'score': round(point['score'], 5), 'retrieval': 'bge-m3 dense+sparse RRF'})
        if len(output) >= limit:
            break
    return output


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approve: bool
    reason: str = Field(min_length=10, max_length=2000)


@router.get('/agent/proposals/{proposal_id}', responses={200: {'model': ProposalResponse}})
async def get_proposal(proposal_id: UUID, request: Request, principal=Depends(require_principal)):
    async with transaction(principal, request.state.request_id) as conn:
        row, _ = await proposal_row(conn, principal, proposal_id)
        return json_value({key: row[key] for key in ('id', 'status', 'sql_text', 'explanation', 'reason', 'preview', 'decided_by', 'decision_reason')})


@router.post('/agent/proposals/{proposal_id}/decision', responses={200: {'model': ProposalDecisionResponse}})
async def decision(proposal_id: UUID, body: Decision, request: Request, idempotency_key: str = Header(min_length=1, max_length=128),
                   principal=Depends(require_principal)):
    async with transaction(principal, request.state.request_id) as conn:
        return json_value(await decide_proposal(conn, principal, proposal_id, body.approve, body.reason, idempotency_key, request.state.request_id))
