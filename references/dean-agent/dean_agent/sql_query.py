"""Constrained read-only SQL access to the deanery schema."""

from datetime import date, datetime
from decimal import Decimal

from pglast import ast, parse_sql
from sqlalchemy import text
from sqlalchemy.orm import Session


class QueryRejected(ValueError):
    pass


def _walk(node):
    if isinstance(node, ast.Node):
        yield node
        for key in node.__slots__:
            yield from _walk(getattr(node, key))
    elif isinstance(node, (tuple, list)):
        for item in node:
            yield from _walk(item)


def validate_read_query(sql: str) -> int:
    if len(sql) > 6000:
        raise QueryRejected("SQL-запрос слишком длинный")
    try:
        statements = parse_sql(sql)
    except Exception as exc:
        raise QueryRejected("Ошибка синтаксиса SQL") from exc
    if len(statements) != 1 or not isinstance(statements[0].stmt, ast.SelectStmt):
        raise QueryRejected("Разрешён один SELECT-запрос")
    root = statements[0].stmt
    limit = root.limitCount
    if not isinstance(limit, ast.A_Const) or not isinstance(limit.val, ast.Integer):
        raise QueryRejected("Укажите явный числовой LIMIT от 1 до 60")
    if not 1 <= limit.val.ival <= 60:
        raise QueryRejected("LIMIT должен быть от 1 до 60")
    for node in _walk(root):
        if isinstance(node, (ast.InsertStmt, ast.UpdateStmt, ast.DeleteStmt,
                             ast.MergeStmt, ast.CallStmt, ast.CopyStmt)):
            raise QueryRejected("Изменения данных в этом инструменте запрещены")
        if isinstance(node, ast.SelectStmt):
            if node.intoClause is not None or node.lockingClause:
                raise QueryRejected("SELECT INTO и блокировка строк запрещены")
            for target in node.targetList or ():
                if any(isinstance(part, ast.A_Star) for part in _walk(target.val)):
                    raise QueryRejected("SELECT * запрещён; перечислите нужные столбцы")
    return limit.val.ival


def execute_read_query(db: Session, sql: str) -> dict:
    limit = validate_read_query(sql)
    # The database role is the real permission boundary. The transaction is also
    # read-only, so a writable CTE or a function with side effects cannot change data.
    db.execute(text("SET TRANSACTION READ ONLY"))
    db.execute(text("SET LOCAL search_path TO deanery, pg_catalog"))
    db.execute(text("SET LOCAL statement_timeout = '5s'"))
    result = db.execute(text(sql))
    columns = list(result.keys())
    rows = []
    for row in result:
        converted = {}
        for name, value in zip(columns, row):
            if isinstance(value, (date, datetime)):
                value = value.isoformat()
            elif isinstance(value, Decimal):
                value = float(value)
            converted[name] = value
        rows.append(converted)
    return {"columns": columns, "rows": rows, "returned": len(rows), "limit": limit}
