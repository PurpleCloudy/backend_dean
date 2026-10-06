import json
import re
from pathlib import Path
from fastapi import APIRouter, Depends, Request, Query
from psycopg import sql
from pydantic import TypeAdapter, ValidationError,create_model
from .auth import require_principal, authorise
from .db import transaction
from .errors import ApiError
from .schemas import dto,read_dto,field_type,json_value,ResourceManifest

manifest = json.loads(Path(__file__).with_name('schema.json').read_text(encoding='utf-8'))
router = APIRouter(tags=['Resources'])
models = {name:{'create':dto(name, spec), 'update':dto(name, spec, True)} for name,spec in manifest['resources'].items()}
read_models={name:read_dto(name,spec) for name,spec in manifest['resources'].items()}
page_models={name:create_model(name+'Page',items=(list[model],...),limit=(int,...),offset=(int,...),has_more=(bool,...)) for name,model in read_models.items()}
private_person_model=read_dto('PersonPrivate',{**manifest['resources']['person'],'read_fields':manifest['resources']['person']['private_read_fields']})


def filter_parameters(spec):
    parameters=[]
    for field in spec['read_fields']:
        schema=TypeAdapter(field_type(spec['fields'][field])).json_schema()
        for operator in ('','eq','ne','lt','lte','gt','gte'):
            parameters.append({'name':field+('__'+operator if operator else ''),'in':'query','required':False,'schema':schema,'description':f"Typed {operator or 'eq'} filter on {field}. At most twelve filters per request."})
    fields='|'.join(re.escape(field)for field in spec['read_fields'])
    parameters.append({'name':'sort','in':'query','required':False,'schema':{'type':'string','pattern':f'^-?(?:{fields})(?:,-?(?:{fields})){{0,2}}$'},'description':'One to three readable fields separated by commas; prefix - for descending. Nulls last; stable key breaks ties.'})
    return parameters


def key_dict(spec, key):
    if not isinstance(key, dict):
        if len(spec['key']) != 1: raise ApiError(422,'composite_key','Supply every key field')
        key = {spec['key'][0]:key}
    if set(key) != set(spec['key']): raise ApiError(422,'invalid_key','Supply exactly the key fields')
    return {k:validate_value(spec,k,v) for k,v in key.items()}


def validate_value(spec, field, value):
    try:
        # Query/path values are strings; JSON keeps explicit null/type semantics.
        kind = field_type(spec['fields'][field])
        if isinstance(value,str) and spec['fields'][field]['type'] in ('integer','smallint','bigint'):
            value=int(value)
        if isinstance(value,str) and spec['fields'][field]['type']=='boolean':
            if value not in ('true','false'): raise ValueError()
            value=value=='true'
        return TypeAdapter(kind).validate_python(value)
    except (ValidationError,ValueError,TypeError):
        raise ApiError(422,'invalid_filter','Invalid key or filter value') from None


async def read_resource(conn, principal, name, key=None, filters=None, limit=50, offset=0):
    await authorise(conn,principal,'read',name)
    spec = manifest['resources'][name]
    if not 1 <= limit <= 500 or not 0 <= offset <= 100000: raise ApiError(422,'invalid_page','Page outside allowed bounds')
    filters = dict(filters or {})
    sorting=filters.pop('sort',None)
    if key is not None: filters.update(key_dict(spec,key))
    if len(filters)>12: raise ApiError(422,'invalid_filter','At most twelve filters allowed')
    params, predicates = [],[]
    operators={'eq':'=','ne':'<>','lt':'<','lte':'<=','gt':'>','gte':'>='}
    for raw,value in filters.items():
        field,op=raw.rsplit('__',1) if '__' in raw else (raw,'eq')
        if field not in spec['read_fields'] or op not in operators: raise ApiError(422,'invalid_filter','Unsupported filter')
        predicates.append(sql.SQL('{} '+operators[op]+' %s').format(sql.Identifier(field)))
        params.append(validate_value(spec,field,value))
    ordering=[]
    requested=sorting.split(',') if isinstance(sorting,str) else []
    if sorting is not None and (not isinstance(sorting,str) or not 1<=len(requested)<=3): raise ApiError(422,'invalid_sort','At most three sort fields allowed')
    for raw in requested+[k for k in spec['key'] if k not in [r.lstrip('-') for r in requested]]:
        field=raw[1:] if raw.startswith('-') else raw
        if field not in spec['read_fields']: raise ApiError(422,'invalid_sort','Unsupported sort field')
        ordering.append(sql.SQL('{} '+('DESC' if raw.startswith('-') else 'ASC')+' NULLS LAST').format(sql.Identifier(field)))
    query=sql.SQL('SELECT {} FROM deanery.{}{} ORDER BY {} LIMIT %s OFFSET %s').format(
        sql.SQL(',').join(map(sql.Identifier,spec['read_fields'])),sql.Identifier(name),
        sql.SQL(' WHERE ')+sql.SQL(' AND ').join(predicates) if predicates else sql.SQL(''),
        sql.SQL(',').join(ordering))
    rows=await (await conn.execute(query,(*params,limit+1,offset))).fetchall()
    if key is not None and not rows: raise ApiError(404,'not_found','Record not found')
    return {'items':rows[:limit],'limit':limit,'offset':offset,'has_more':len(rows)>limit}


async def mutate_resource(conn, principal, name, operation, key=None, values=None):
    await authorise(conn,principal,operation,name,key)
    spec=manifest['resources'][name]
    if operation not in ('create','update','delete'): raise ApiError(422,'invalid_operation','Unsupported operation')
    data={}
    if operation != 'delete':
        try: data=models[name][operation].model_validate(values or {}).model_dump(exclude_unset=True)
        except ValidationError as exc: raise ApiError(422,'validation_error','Invalid fields',[{'location':list(e['loc']),'type':e['type']} for e in exc.errors()]) from None
        if not data: raise ApiError(422,'empty_change','Provide at least one field')
    if name=='document_request':
        if 'student' in principal.roles:
            if operation!='create' or data.get('student_id')!=principal.student_id or 'processed_by_id' in data: raise ApiError(403,'forbidden','Student may request their own document')
            initial=await (await conn.execute("SELECT request_status_id FROM deanery.request_status WHERE NOT is_final ORDER BY request_status_id LIMIT 1")).fetchone()
            if data.get('request_status_id')!=initial['request_status_id']: raise ApiError(403,'forbidden','Initial request status required')
    returning=sql.SQL(',').join(map(sql.Identifier,spec['read_fields']))
    table=sql.Identifier('deanery',name)
    if operation=='create':
        query=sql.SQL('INSERT INTO {} ({}) VALUES ({}) RETURNING {}').format(table,sql.SQL(',').join(map(sql.Identifier,data)),sql.SQL(',').join(sql.Placeholder() for _ in data),returning)
        params=tuple(data.values())
    else:
        keys=key_dict(spec,key)
        where=sql.SQL(' AND ').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in keys)
        if operation=='update':
            query=sql.SQL('UPDATE {} SET {} WHERE {} RETURNING {}').format(table,sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in data),where,returning)
            params=(*data.values(),*keys.values())
        else:
            query=sql.SQL('DELETE FROM {} WHERE {} RETURNING {}').format(table,where,returning);params=tuple(keys.values())
    row=await(await conn.execute(query,params)).fetchone()
    if row is None: raise ApiError(404,'not_found','Record not found')
    return row


@router.get('/schema',responses={200:{'model':ResourceManifest}})
async def schema(principal=Depends(require_principal)):
    return {'version':manifest['version'],'resources':{n:s for n,s in manifest['resources'].items() if principal.roles.intersection(s['read_roles'])}}


async def read_private_person(conn,principal,person_id):
    if principal.person_id!=person_id and not principal.roles.intersection({'admin','director','dean_staff'}):
        raise ApiError(403,'forbidden','Private details require own identity or scoped deanery access')
    allowed=await(await conn.execute('SELECT backend.private_person_allowed(%s) AS ok',(person_id,))).fetchone()
    if not allowed['ok']: raise ApiError(403,'forbidden','Private details are outside your institute')
    await authorise(conn,principal,'read','person',person_id)
    fields=manifest['resources']['person']['private_read_fields']
    record=await(await conn.execute(sql.SQL('SELECT {} FROM deanery.person WHERE person_id=%s').format(sql.SQL(',').join(map(sql.Identifier,fields))),(person_id,))).fetchone()
    if not record: raise ApiError(404,'not_found','Person not found')
    return record


@router.get('/people/{person_id}/private',responses={200:{'model':private_person_model}},description='Human-only private details. Own person or scoped deanery staff/director/admin. Agent SQL projection never includes these fields.')
async def private_person(person_id:int,request:Request,principal=Depends(require_principal)):
    async with transaction(principal,request.state.request_id,readonly=True) as conn:
        return json_value(await read_private_person(conn,principal,person_id))


def register(name,spec):
    path='/resources/'+name
    async def read(request:Request, limit:int=Query(50,ge=1,le=500),offset:int=Query(0,ge=0,le=100000),principal=Depends(require_principal)):
        filters={k:v for k,v in request.query_params.items() if k not in ('limit','offset')}
        async with transaction(principal,request.state.request_id,readonly=True) as conn:
            return json_value(await read_resource(conn,principal,name,filters=filters,limit=limit,offset=offset))
    router.add_api_route(path,read,methods=['GET'],name='read_'+name,response_model=page_models[name],description=f"Roles: {', '.join(spec['read_roles'])}. Up to 12 typed filters (eq/ne/lt/lte/gt/gte), at most 3 sort fields separated by commas; '-' means descending; nulls last; stable key {spec['key']} breaks ties.",openapi_extra={'parameters':filter_parameters(spec)})
    for operation,method in [('create','POST'),('update','PATCH'),('delete','DELETE')]:
        if operation not in spec['operations']: continue
        def endpoint(op):
            async def mutate(request:Request, principal=Depends(require_principal)):
                values=await request.json() if op!='delete' else None
                keys={k:v for k,v in request.query_params.items()}
                async with transaction(principal,request.state.request_id) as conn:
                    return json_value(await mutate_resource(conn,principal,name,op,key=keys or None,values=values))
            return mutate
        extra={'requestBody':{'required':True,'content':{'application/json':{'schema':models[name][operation].model_json_schema()}}}} if operation!='delete' else {}
        if operation in ('update','delete'):
            extra['parameters']=[{'name':key,'in':'query','required':True,'schema':TypeAdapter(field_type(spec['fields'][key])).json_schema(),'description':spec['fields'][key].get('description') or key} for key in spec['key']]
        router.add_api_route(path,endpoint(operation),methods=[method],name=operation+'_'+name,response_model=read_models[name],description=f"Roles: {', '.join(spec.get('operation_roles',{}).get(operation,spec['write_roles']))}. Keys in query parameters: {spec['key']}. Null is distinct from omitted.",openapi_extra=extra)

for _name,_spec in manifest['resources'].items(): register(_name,_spec)
