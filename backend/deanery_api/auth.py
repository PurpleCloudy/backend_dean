import asyncio
import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPBearer,APIKeyCookie
from pydantic import BaseModel, ConfigDict, Field
from .config import get_settings
from .db import get_pool, transaction
from .errors import ApiError

router = APIRouter(prefix="/auth", tags=["Authentication"])
bearer_scheme=HTTPBearer(auto_error=False,scheme_name='BearerSession',description='Short-lived access token bound to a current active server session.')
refresh_scheme=APIKeyCookie(name='deanery_refresh',auto_error=False,scheme_name='RotatingRefresh',description='HttpOnly refresh cookie; Origin and X-CSRF-Token are also required.')
origin_parameter={'name':'Origin','in':'header','required':True,'schema':{'type':'string'},'description':'An explicitly allowed browser origin.'}
hasher = PasswordHasher()
password_slots = asyncio.Semaphore(4)
dummy_hash = hasher.hash(secrets.token_urlsafe(32))


@dataclass(frozen=True)
class Principal:
    user_id: int
    login: str
    roles: frozenset[str]
    person_id: int | None
    institute_ids: frozenset[int]
    student_id: int | None
    teacher_id: int | None


async def load_principal(conn, user_id):
    row = await (await conn.execute("SELECT * FROM backend.identity(%s)", (user_id,))).fetchone()
    if not row or not row["is_active"]:
        raise ApiError(401, "inactive_session", "Authentication required")
    return Principal(row["user_id"], row["login"], frozenset([row["role_code"]]), row["person_id"], frozenset(row["institute_ids"] or []), row["student_id"], row["teacher_id"])


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def access_token(principal, session_id):
    now = int(time.time())
    s = get_settings()
    return jwt.encode({"sub": str(principal.user_id), "sid": str(session_id), "iat": now, "exp": now + s.access_ttl_seconds, "iss": "deanery-api", "aud": "deanery-api", "jti": str(uuid.uuid4())}, s.access_token_key, algorithm="HS256")


async def require_principal(request: Request,_credentials=Depends(bearer_scheme)):
    header = request.headers.get("authorization", "")
    try:
        if not header.startswith("Bearer "):
            raise ValueError()
        claims = jwt.decode(header[7:], get_settings().access_token_key, algorithms=["HS256"], audience="deanery-api", issuer="deanery-api", options={"require": ["sub", "sid", "exp", "iat", "jti"]})
        user_id, session_id = int(claims["sub"]), uuid.UUID(claims["sid"])
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise ApiError(401, "invalid_token", "Authentication required") from None
    async with get_pool().connection() as conn:
        session = await (await conn.execute("SELECT user_id FROM backend.sessions WHERE id=%s AND user_id=%s AND revoked_at IS NULL AND expires_at>now()", (session_id, user_id))).fetchone()
        if not session:
            raise ApiError(401, "inactive_session", "Authentication required")
        principal = await load_principal(conn, user_id)
    request.state.session_id = session_id
    request.state.principal = principal
    return principal


async def authorise(conn, principal, action, resource, record_id=None, payload=None):
    from .resources import manifest, key_dict
    spec = manifest["resources"].get(resource)
    if not spec:
        raise ApiError(404, "unknown_resource", "Resource not found")
    roles = spec.get('operation_roles',{}).get(action,spec["read_roles"] if action == "read" else spec["write_roles"])
    if not principal.roles.intersection(roles) or (action != "read" and action not in spec["operations"]):
        raise ApiError(403, "forbidden", "Operation is not permitted")
    if record_id is not None and spec["kind"] == "table":
        from psycopg import sql
        keys = key_dict(spec, record_id)
        statement = sql.SQL("SELECT 1 FROM deanery.{} WHERE {} LIMIT 1").format(sql.Identifier(resource), sql.SQL(" AND ").join(sql.SQL("{}=%s").format(sql.Identifier(k)) for k in keys))
        if not await (await conn.execute(statement, tuple(keys.values()))).fetchone():
            raise ApiError(404, "not_found", "Record not found")


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=1024)


class AccessTokens(BaseModel):
    access_token: str
    token_type: Literal['bearer']
    expires_in: int


class LoginTokens(AccessTokens):
    csrf_token: str


def check_origin(request):
    origin = request.headers.get("origin")
    if not origin or ("*" not in get_settings().allowed_origins and origin not in get_settings().allowed_origins):
        raise ApiError(403, "invalid_origin", "Allowed Origin header required")


def put_cookie(response, refresh):
    s = get_settings()
    response.set_cookie("deanery_refresh", refresh, httponly=True, secure=not s.development, samesite="strict", path="/api/v1/auth", max_age=s.refresh_ttl_seconds)


async def verify_password(encoded, password):
    async with password_slots:
        try:
            return await asyncio.to_thread(hasher.verify, encoded, password)
        except (VerificationError, InvalidHashError):
            return False


@router.post("/login",responses={200:{'model':LoginTokens}},openapi_extra={'parameters':[origin_parameter]})
async def login(body: Login, request: Request, response: Response):
    check_origin(request)
    # Independent shared account/IP windows count known and unknown attempts equally.
    settings=get_settings()
    buckets={digest('account:'+body.login.casefold()):settings.login_account_limit,
             digest('ip:'+(request.client.host if request.client else 'unknown')):settings.login_ip_limit}
    exceeded=False
    async with get_pool().connection() as conn:
        async with conn.transaction():
            # Stable key order avoids cross-worker lock inversion on shared buckets.
            for key in sorted(buckets):
                rate=await(await conn.execute("INSERT INTO backend.login_attempts(key,started_at,attempts) VALUES(%s,now(),1) ON CONFLICT(key) DO UPDATE SET attempts=CASE WHEN backend.login_attempts.started_at<=now()-(%s*interval '1 second') THEN 1 ELSE backend.login_attempts.attempts+1 END, started_at=CASE WHEN backend.login_attempts.started_at<=now()-(%s*interval '1 second') THEN now() ELSE backend.login_attempts.started_at END RETURNING attempts",(key,settings.login_window_seconds,settings.login_window_seconds))).fetchone()
                exceeded=exceeded or rate['attempts']>buckets[key]
            row=await(await conn.execute("SELECT * FROM backend.login_record(%s)",(body.login,))).fetchone()
    if exceeded: raise ApiError(429,'login_rate_limit','Try again later')
    valid = await verify_password(row["password_hash"] if row else dummy_hash, body.password)
    if not row or not valid or not row["is_active"]:
        raise ApiError(401, "invalid_credentials", "Invalid login or password")
    refresh, csrf, sid = secrets.token_urlsafe(48), secrets.token_urlsafe(32), uuid.uuid4()
    async with get_pool().connection() as conn:
        principal = await load_principal(conn, row["user_id"])
        await conn.execute("INSERT INTO backend.sessions(id,user_id,csrf_hash,expires_at) VALUES (%s,%s,%s,now()+(%s*interval '1 second'))", (sid, principal.user_id, digest(csrf), get_settings().refresh_ttl_seconds))
        await conn.execute("INSERT INTO backend.refresh_tokens(token_hash,session_id) VALUES(%s,%s)", (digest(refresh), sid))
        await conn.execute("SELECT backend.touch_login(%s)", (principal.user_id,))
    put_cookie(response, refresh)
    return {"access_token": access_token(principal, sid), "token_type": "bearer", "expires_in": get_settings().access_ttl_seconds, "csrf_token": csrf}


@router.post("/refresh",responses={200:{'model':AccessTokens}},openapi_extra={'parameters':[origin_parameter,{'name':'X-CSRF-Token','in':'header','required':True,'schema':{'type':'string'},'description':'CSRF token returned by login for this refresh session.'}]})
async def refresh(request: Request, response: Response,_refresh_cookie=Depends(refresh_scheme)):
    check_origin(request)
    token = request.cookies.get("deanery_refresh", "")
    csrf = request.headers.get("x-csrf-token", "")
    error, output = None, None
    async with get_pool().connection() as conn:
        row = await (await conn.execute("SELECT s.*,t.used_at FROM backend.refresh_tokens t JOIN backend.sessions s ON s.id=t.session_id WHERE t.token_hash=%s FOR UPDATE OF s,t", (digest(token),))).fetchone()
        if not row or row["revoked_at"] or row["expires_at"] <= datetime.now(timezone.utc):
            error = ApiError(401, "invalid_refresh", "Authentication required")
        elif not hmac.compare_digest(row["csrf_hash"], digest(csrf)):
            error = ApiError(403, "invalid_csrf", "CSRF token required")
        elif row["used_at"]:
            await conn.execute("UPDATE backend.sessions SET revoked_at=now() WHERE id=%s", (row["id"],))
            error = ApiError(401, "refresh_reused", "Session revoked")
        else:
            principal = await load_principal(conn, row["user_id"])
            replacement = secrets.token_urlsafe(48)
            await conn.execute("UPDATE backend.refresh_tokens SET used_at=now() WHERE token_hash=%s", (digest(token),))
            await conn.execute("INSERT INTO backend.refresh_tokens(token_hash,session_id) VALUES(%s,%s)", (digest(replacement), row["id"]))
            output = {"access_token": access_token(principal, row["id"]), "token_type": "bearer", "expires_in": get_settings().access_ttl_seconds}
    if error:
        raise error
    put_cookie(response, replacement)
    return output


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, principal=Depends(require_principal)):
    async with get_pool().connection() as conn:
        await conn.execute("UPDATE backend.sessions SET revoked_at=now() WHERE id=%s AND user_id=%s", (request.state.session_id, principal.user_id))
    response.delete_cookie("deanery_refresh", path="/api/v1/auth")


@router.get("/me",responses={200:{'model':Principal}})
async def me(principal=Depends(require_principal)):
    return principal


@router.post("/logout-all", status_code=204)
async def logout_all(principal=Depends(require_principal)):
    async with get_pool().connection() as conn:
        await conn.execute("UPDATE backend.sessions SET revoked_at=now() WHERE user_id=%s", (principal.user_id,))


class PasswordChange(BaseModel):
    model_config=ConfigDict(extra='forbid')
    current_password:str=Field(min_length=1,max_length=1024)
    new_password:str=Field(min_length=12,max_length=1024)


@router.post('/password',status_code=204)
async def change_password(body:PasswordChange,request:Request,principal=Depends(require_principal)):
    async with transaction(principal,request.state.request_id) as conn:
        row=await(await conn.execute('SELECT * FROM backend.login_record(%s)',(principal.login,))).fetchone()
        if not await verify_password(row['password_hash'],body.current_password):
            raise ApiError(401,'invalid_credentials','Current password is incorrect')
        async with password_slots:
            encoded=await asyncio.to_thread(hasher.hash,body.new_password)
        await conn.execute('SELECT backend.change_password(%s,%s)',(principal.user_id,encoded))
        await conn.execute('UPDATE backend.sessions SET revoked_at=now() WHERE user_id=%s',(principal.user_id,))
