import asyncio
import importlib
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from . import auth,resources,workflows,reporting,db
from .config import get_settings
from .errors import install_handlers,ApiError,common_error_responses,ErrorEnvelope
from .schemas import LiveHealth,CoreHealth,ReadinessHealth


class BodyLimit:
    def __init__(self,app): self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        headers=dict(scope['headers'])
        limit=get_settings().max_upload_bytes+65536 if scope['path'].startswith('/api/v1/files') else 650000 if scope['path']=='/api/v1/internal/agent/complete' else 65536
        length=headers.get(b'content-length')
        try:
            declared=int(length) if length else None
            if declared is not None and declared<0: raise ValueError()
        except ValueError:
            response=JSONResponse({'error':{'code':'invalid_content_length','message':'Invalid body length'},'request_id':scope.get('state',{}).get('request_id')},status_code=400)
            return await response(scope,receive,send)
        if declared is not None and declared>limit:
            response=JSONResponse({'error':{'code':'body_too_large','message':'Request body exceeds limit'},'request_id':scope.get('state',{}).get('request_id')},status_code=413)
            return await response(scope,receive,send)
        consumed=0
        async def bounded_receive():
            nonlocal consumed
            message=await receive()
            if message['type']=='http.request':
                consumed+=len(message.get('body',b''))
                if consumed>limit: raise ApiError(413,'body_too_large','Request body exceeds limit')
                if not message.get('more_body',False) and declared is not None and consumed!=declared:
                    raise ApiError(400,'invalid_content_length','Body length does not match Content-Length')
            return message
        return await self.app(scope,bounded_receive,send)


@asynccontextmanager
async def lifespan(app):
    await db.open_pool()
    services=[]
    try:
        for name in ('agent_gateway','storage','jobs'):
            module=importlib.import_module('.'+name,__package__)
            if hasattr(module,'start_services'):
                await module.start_services(app)
                services.append(module)
        yield
    finally:
        for module in reversed(services): await module.stop_services(app)
        await db.close_pool()


app=FastAPI(title='Deanery API',version='1.0.0',lifespan=lifespan,responses=common_error_responses)
install_handlers(app)
app.add_middleware(CORSMiddleware,allow_origins=get_settings().allowed_origins,allow_credentials=True,allow_methods=['GET','POST','PATCH','DELETE'],allow_headers=['Authorization','Content-Type','X-CSRF-Token','Idempotency-Key'],expose_headers=['X-Request-ID','Content-Disposition'])
app.add_middleware(BodyLimit)


@app.middleware('http')
async def correlation(request,call_next):
    request.state.request_id=str(uuid.uuid4())
    start=time.monotonic()
    response=await call_next(request)
    response.headers['X-Request-ID']=request.state.request_id
    logging.getLogger('deanery').info('request id=%s method=%s status=%s duration_ms=%.1f',request.state.request_id,request.method,response.status_code,(time.monotonic()-start)*1000)
    return response


for module in (auth,resources,workflows,reporting): app.include_router(module.router,prefix='/api/v1')
for name in ('agent_gateway','agent_tools','files','jobs'):
    try: module=importlib.import_module('.'+name,__package__)
    except ModuleNotFoundError as exc:
        if exc.name != __package__+'.'+name: raise
        continue
    if hasattr(module,'router'): app.include_router(module.router,prefix='/api/v1')


@app.get('/health/live',tags=['Health'],responses={200:{'model':LiveHealth}})
async def live(): return {'status':'ok'}


@app.get('/health/ready',tags=['Health'],responses={200:{'model':ReadinessHealth},503:{'model':ReadinessHealth|ErrorEnvelope}})
async def ready():
    core=await core_ready()
    from . import storage,agent_gateway
    parts=await asyncio.gather(storage.health(),agent_gateway.health())
    components={'database':{'status':'ready'},**parts[0],**parts[1]}
    healthy=all(v['status']=='ready' for v in components.values())
    return JSONResponse({'status':'ready' if healthy else 'unavailable','schema':core['schema'],'components':components},status_code=200 if healthy else 503)


@app.get('/health/core',tags=['Health'],responses={200:{'model':CoreHealth}})
async def core_ready():
    async with db.get_pool().connection() as conn:
        row=await(await conn.execute("SELECT version_num FROM alembic_version")).fetchone()
        if row['version_num']!='0002':
            from .errors import ApiError
            raise ApiError(503,'migration_required','Database migrations required')
    return {'status':'ready','schema':row['version_num']}


if __name__=='__main__':
    import uvicorn
    server=uvicorn.Server(uvicorn.Config(app,host='0.0.0.0',port=8000,loop='asyncio',forwarded_allow_ips=get_settings().trusted_proxy_ips))
    asyncio.run(server.serve(),loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None)
