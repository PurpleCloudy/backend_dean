from contextlib import asynccontextmanager
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from .config import get_settings

pool: AsyncConnectionPool | None = None


async def open_pool():
    global pool
    s = get_settings()
    pool = AsyncConnectionPool(s.database_url, min_size=s.pool_min_size, max_size=s.pool_max_size, open=False, kwargs={"row_factory": dict_row}, timeout=10)
    await pool.open(wait=True)
    async with pool.connection() as conn:
        role = await (await conn.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")).fetchone()
        owner = await (await conn.execute("SELECT EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspname IN('deanery','backend') AND n.nspowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)) OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN('deanery','backend') AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)) AS owned")).fetchone()
        if not role or role["rolsuper"] or role["rolbypassrls"] or not owner or owner["owned"]:
            raise RuntimeError("Runtime database role must be nonowner, nonsuperuser and NOBYPASSRLS")


async def close_pool():
    if pool:
        await pool.close()


def get_pool():
    if pool is None:
        raise RuntimeError("Database pool is not open")
    return pool


@asynccontextmanager
async def transaction(principal, request_id=None, readonly=False):
    async with get_pool().connection() as conn:
        try:
            async with conn.transaction():
                await conn.execute("SELECT backend.set_actor(%s,%s)", (principal.user_id if principal else None, str(request_id or "")))
            async with conn.transaction():
                await conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
                if readonly: await conn.execute("SET TRANSACTION READ ONLY")
                await conn.execute("SELECT set_config('search_path','deanery,backend,public',true)")
                await conn.execute("SET LOCAL TIME ZONE 'Europe/Moscow'")
                await conn.execute("SELECT set_config('statement_timeout',%s,true)", (str(get_settings().statement_timeout_ms),))
                await conn.execute("SELECT set_config('lock_timeout','5000',true)")
                if not readonly:
                    # ponytail: one university-wide domain write lock; partition by invariant when measured throughput needs it.
                    await conn.execute("SELECT pg_advisory_xact_lock(617221)")
                yield conn
        finally:
            try:
                async with conn.transaction():
                    await conn.execute("SELECT backend.set_actor(NULL,'')")
            except BaseException:
                await conn.close()
                raise
