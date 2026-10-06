"""Reviewable, tightly scoped INSERT/UPDATE proposals for deanery."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from pglast import ast, parse_sql
from pglast.stream import RawStream
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from dean_agent.models import AuditLog, SQLChangeProposal


class SQLChangeError(ValueError):
    pass


# Direct row edits are limited to independent work records. Student movement,
# orders, leaves and scholarships must use domain procedures/workflows.
ALLOWED = {
    "document_request": {
        "pk": "request_id",
        "insert": {"student_id", "document_type_id", "request_status_id", "copies", "purpose"},
        "update": {"request_status_id", "copies", "purpose", "processed_by_id", "completed_at"},
    },
    "academic_work": {
        "pk": "work_id",
        "insert": {"student_id", "item_id", "topic", "supervisor_id", "reviewer_id", "order_id"},
        "update": {"topic", "supervisor_id", "reviewer_id", "order_id"},
    },
    "grade": {
        "pk": "grade_id",
        "insert": {"sheet_id", "student_id", "points", "is_absent"},
        "update": {"points", "is_absent"},
    },
    "attendance": {
        "pk": "attendance_id",
        "insert": {"slot_id", "student_id", "lesson_date", "is_present", "absence_reason"},
        "update": {"is_present", "absence_reason"},
    },
    "grade_sheet": {
        "pk": "sheet_id",
        "insert": {"sheet_number", "item_id", "group_id", "examiner_id", "stage",
                   "sheet_kind", "issue_date", "exam_date", "closed_date", "status"},
        "update": {"examiner_id", "exam_date", "closed_date", "status"},
    },
    "practice_placement": {
        "pk": "placement_id",
        "insert": {"student_id", "item_id", "supervisor_id", "org_contact_id",
                   "start_date", "end_date", "order_id"},
        "update": {"supervisor_id", "org_contact_id", "start_date", "end_date", "order_id"},
    },
    "teaching_assignment": {
        "pk": "assignment_id",
        "insert": {"item_id", "group_id", "teacher_id", "lesson_type_id", "subgroup"},
        "update": {"teacher_id", "lesson_type_id", "subgroup"},
    },
    "schedule_slot": {
        "pk": "slot_id",
        "insert": {"assignment_id", "classroom_id", "weekday", "pair_number",
                   "week_parity", "term_id", "valid_from", "valid_to"},
        "update": {"classroom_id", "weekday", "pair_number", "week_parity",
                   "valid_from", "valid_to"},
    },
    "organization_contact": {
        "pk": "org_contact_id",
        "insert": {"organization_id", "last_name", "first_name", "middle_name",
                   "job_title", "phone", "email"},
        "update": {"organization_id", "last_name", "first_name", "middle_name",
                   "job_title", "phone", "email"},
    },
    "contact_person": {
        "pk": "contact_person_id",
        "insert": {"last_name", "first_name", "middle_name", "phone", "email"},
        "update": {"last_name", "first_name", "middle_name", "phone", "email"},
    },
}


@dataclass(frozen=True)
class ParsedChange:
    sql: str
    operation: str
    table: str
    pk: str
    pk_value: int | None
    columns: tuple[str, ...]


def _literal(node) -> bool:
    return isinstance(node, ast.A_Const)


def parse_change(sql_text: str) -> ParsedChange:
    if len(sql_text) > 4000:
        raise SQLChangeError("SQL слишком длинный")
    try:
        statements = parse_sql(sql_text)
    except Exception as exc:
        raise SQLChangeError("Ошибка синтаксиса SQL") from exc
    if len(statements) != 1:
        raise SQLChangeError("Нужна одна команда INSERT или UPDATE")
    node = statements[0].stmt
    if not isinstance(node, (ast.InsertStmt, ast.UpdateStmt)):
        raise SQLChangeError("В предложении разрешены только INSERT и UPDATE")
    relation = node.relation
    table = relation.relname
    if relation.schemaname not in (None, "deanery") or relation.alias or table not in ALLOWED:
        raise SQLChangeError("Таблица не входит в список подтверждаемых изменений")
    spec = ALLOWED[table]
    if node.withClause or node.returningClause:
        raise SQLChangeError("WITH и RETURNING в предложении запрещены")
    if isinstance(node, ast.InsertStmt):
        source = node.selectStmt
        if node.onConflictClause or not isinstance(source, ast.SelectStmt) or not source.valuesLists or len(source.valuesLists) != 1:
            raise SQLChangeError("INSERT должен содержать одну строку VALUES без ON CONFLICT")
        columns = tuple(item.name for item in node.cols or ())
        if not columns or len(columns) != len(set(columns)) or not set(columns) <= spec["insert"]:
            raise SQLChangeError("Недопустимые столбцы INSERT")
        values = source.valuesLists[0]
        if len(values) != len(columns) or not all(_literal(value) for value in values):
            raise SQLChangeError("INSERT допускает только простые значения")
        operation, pk_value = "INSERT", None
    else:
        if node.fromClause:
            raise SQLChangeError("UPDATE ... FROM запрещён")
        columns = tuple(item.name for item in node.targetList or ())
        if not columns or len(columns) != len(set(columns)) or not set(columns) <= spec["update"]:
            raise SQLChangeError("Недопустимые столбцы UPDATE")
        if any(item.indirection or not _literal(item.val) for item in node.targetList):
            raise SQLChangeError("UPDATE допускает только простые значения")
        where = node.whereClause
        if (not isinstance(where, ast.A_Expr) or len(where.name) != 1 or
                where.name[0].sval != "=" or not isinstance(where.lexpr, ast.ColumnRef) or
                len(where.lexpr.fields) != 1 or where.lexpr.fields[0].sval != spec["pk"] or
                not isinstance(where.rexpr, ast.A_Const) or
                not isinstance(where.rexpr.val, ast.Integer) or where.rexpr.val.ival < 1):
            raise SQLChangeError(f"UPDATE должен выбирать одну запись: WHERE {spec['pk']} = <id>")
        operation, pk_value = "UPDATE", where.rexpr.val.ival
    return ParsedChange(RawStream()(node), operation, table, spec["pk"], pk_value, columns)


def _writer_role(db: Session) -> None:
    db.execute(text("SET LOCAL ROLE deanery_agent"))
    db.execute(text("SET LOCAL search_path TO deanery, pg_catalog"))
    db.execute(text("SET LOCAL statement_timeout = '5s'"))
    db.execute(text("SET LOCAL lock_timeout = '1s'"))


def _current(db: Session, parsed: ParsedChange) -> dict | None:
    if parsed.pk_value is None:
        return None
    return db.connection().exec_driver_sql(
        f"SELECT to_jsonb(t) FROM deanery.{parsed.table} AS t WHERE {parsed.pk} = %s FOR UPDATE",
        (parsed.pk_value,),
    ).scalar_one_or_none()


def _apply(db: Session, parsed: ParsedChange) -> dict:
    result = db.connection().exec_driver_sql(
        f"{parsed.sql} RETURNING to_jsonb({parsed.table})"
    )
    rows = result.scalars().all()
    if len(rows) != 1:
        raise SQLChangeError("Команда должна затронуть ровно одну запись")
    return rows[0]


def propose_sql_change(db: Session, *, sql_text: str, explanation: str, reason: str,
                       actor_id: str, agent_run_id: str) -> SQLChangeProposal:
    parsed = parse_change(sql_text)
    if len(explanation.strip()) < 15 or len(reason.strip()) < 10:
        raise SQLChangeError("Нужны описание действия и причина изменения")
    _writer_role(db)
    before = _current(db, parsed)
    if parsed.operation == "UPDATE" and before is None:
        raise SQLChangeError("Запись для UPDATE не найдена")
    savepoint = db.begin_nested()
    try:
        after = _apply(db, parsed)
    finally:
        savepoint.rollback()
    if parsed.operation == "UPDATE" and before == after:
        raise SQLChangeError("Команда ничего не меняет")
    if parsed.operation == "INSERT":
        after = {key: value for key, value in after.items() if key != parsed.pk}
    db.connection().exec_driver_sql("SET LOCAL ROLE NONE")
    db.connection().exec_driver_sql("SET LOCAL search_path TO public, pg_catalog")
    preview = {"operation": parsed.operation, "table": parsed.table,
               "affected_rows": 1, "before": before, "after": after,
               "changed_columns": list(parsed.columns),
               "related_effects": ["Запись в журнал аудита при подтверждении"],
               "note": "Идентификатор и поля с текущим временем присваиваются заново при подтверждении" if parsed.operation == "INSERT" else None}
    proposal = SQLChangeProposal(sql_text=parsed.sql, explanation=explanation.strip(),
                                 reason=reason.strip(), preview=preview, actor_id=actor_id,
                                 agent_run_id=agent_run_id)
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return proposal


def decide_sql_change(db: Session, *, proposal_id: uuid.UUID, approver: str,
                      decision_reason: str, approve: bool) -> SQLChangeProposal:
    if len(decision_reason.strip()) < 10:
        raise SQLChangeError("Сотрудник должен указать причину решения")
    proposal = db.scalar(select(SQLChangeProposal).where(SQLChangeProposal.id == proposal_id).with_for_update())
    if proposal is None:
        raise SQLChangeError("Предложение не найдено")
    if proposal.status != "pending":
        raise SQLChangeError("Предложение уже обработано")
    parsed = parse_change(proposal.sql_text)
    _writer_role(db)
    user_id = db.connection().exec_driver_sql(
        "SELECT u.user_id FROM deanery.app_user u JOIN deanery.app_role r ON r.role_id = u.role_id "
        "WHERE u.login = %s AND u.is_active AND r.code IN ('admin', 'director', 'dean_staff')",
        (approver,),
    ).scalar_one_or_none()
    if user_id is None:
        raise SQLChangeError("Подтверждающий должен быть действующим сотрудником деканата")
    if approve:
        before = _current(db, parsed)
        if parsed.operation == "UPDATE" and before != proposal.preview["before"]:
            raise SQLChangeError("Запись изменилась после предпросмотра; создайте новое предложение")
        request_id = db.connection().exec_driver_sql(
            "INSERT INTO deanery.agent_request (user_id, request_text, response_text, status) "
            "VALUES (%s, %s, %s, 'success') RETURNING agent_request_id",
            (user_id, proposal.reason, proposal.explanation),
        ).scalar_one()
        db.connection().exec_driver_sql("SELECT set_config('app.user_id', %s, true)", (str(user_id),))
        db.connection().exec_driver_sql("SELECT set_config('app.agent_request_id', %s, true)", (str(request_id),))
        applied = _apply(db, parsed)
        proposal.preview = {**proposal.preview, "applied": applied}
    db.connection().exec_driver_sql("SET LOCAL ROLE NONE")
    db.connection().exec_driver_sql("SET LOCAL search_path TO public, pg_catalog")
    proposal.status = "approved" if approve else "rejected"
    proposal.decided_by = approver
    proposal.decision_reason = decision_reason.strip()
    proposal.resolved_at = datetime.now(timezone.utc)
    db.add(AuditLog(actor_id=approver, agent_run_id=proposal.agent_run_id,
                    action="approve_sql_change" if approve else "reject_sql_change",
                    entity_type="sql_change_proposal", entity_id=str(proposal.id),
                    old_value=proposal.preview["before"],
                    new_value=proposal.preview.get("applied") if approve else None))
    db.commit()
    db.refresh(proposal)
    return proposal
