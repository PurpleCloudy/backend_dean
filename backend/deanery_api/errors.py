import logging
import json
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
import psycopg
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, ConfigDict, JsonValue

log = logging.getLogger("deanery")


class ErrorIssue(BaseModel):
    location: list[str | int]
    type: str


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: list[ErrorIssue] | dict[str, JsonValue] | None = None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    error: ErrorInfo
    request_id: str | None


common_error_responses={status:{'model':ErrorEnvelope,'description':description} for status,description in {
    400:'Malformed request',401:'Authentication required',403:'Operation forbidden',404:'Record or route not found',
    405:'Method not allowed',409:'Conflict with current data',413:'Request body too large',415:'Unsupported media type',
    422:'Request validation failed',429:'Request limit exceeded',500:'Internal error',502:'Upstream error',503:'Service unavailable',504:'Upstream timeout'}.items()}


class ApiError(HTTPException):
    def __init__(self, status, code, message, details=None):
        super().__init__(status_code=status,detail=message)
        self.status, self.code, self.message, self.details = status, code, message, details


def install_handlers(app):
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}, "request_id": getattr(request.state, "request_id", None)})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc):
        if any(e['type']=='json_invalid' for e in exc.errors()):
            return await api_error(request,ApiError(400,'invalid_json','Malformed JSON body'))
        return await api_error(request, ApiError(422, "validation_error", "Invalid request", [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()]))

    @app.exception_handler(json.JSONDecodeError)
    async def malformed_json(request,exc):
        return await api_error(request,ApiError(400,'invalid_json','Malformed JSON body'))

    @app.exception_handler(HTTPException)
    async def http_error(request,exc):
        return await api_error(request,ApiError(exc.status_code,'http_error','Request cannot be completed'))

    @app.exception_handler(Exception)
    async def unexpected(request,exc):
        log.error('unexpected_error request_id=%s type=%s',getattr(request.state,'request_id',''),type(exc).__name__)
        result=await api_error(request,ApiError(500,'internal_error','Request cannot be completed'))
        result.headers['X-Request-ID']=getattr(request.state,'request_id','')
        return result

    @app.exception_handler(psycopg.Error)
    async def database_error(request: Request, exc):
        state = exc.sqlstate or ""
        codes = {"23505": (409, "duplicate"), "23503": (409, "relationship_conflict"), "23514": (422, "constraint_violation"), "23502": (422, "required_field"), "23P01": (409, "period_conflict"), "40001": (409, "concurrent_change"), "40P01": (409, "concurrent_change"), "42501": (403, "forbidden"), "57014": (503, "query_timeout"), "P0001": (409, "domain_conflict")}
        codes['P0002'] = (404, 'not_found')
        status, code = codes.get(state, (422, "invalid_value") if state.startswith("22") else (503, "database_unavailable"))
        log.warning("database_error request_id=%s sqlstate=%s", getattr(request.state, "request_id", ""), state)
        return await api_error(request, ApiError(status, code, "Operation cannot be completed"))

    @app.exception_handler(PoolTimeout)
    async def pool_timeout(request,exc):
        return await api_error(request,ApiError(503,'database_busy','Database is temporarily busy'))
