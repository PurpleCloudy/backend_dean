from fastapi import APIRouter,Depends,Request,Query
from typing import Union
from pydantic import create_model
from .auth import require_principal
from .db import transaction
from .resources import manifest,read_resource,read_models,filter_parameters
from .schemas import json_value
from .errors import ApiError

router=APIRouter(prefix='/reports',tags=['Reports'])
report_models={name:create_model(name+'Report',items=(list[read_models[name]],...),limit=(int,...)) for name,spec in manifest['resources'].items() if spec['kind']=='view'}
ReportResponse=Union[tuple(report_models.values())]
report_parameters={}
for _name in report_models:
    for _parameter in filter_parameters(manifest['resources'][_name]):
        _entry=report_parameters.setdefault(_parameter['name'],{'schemas':[],'reports':[]})
        if _parameter['schema'] not in _entry['schemas']:_entry['schemas'].append(_parameter['schema'])
        _entry['reports'].append(_name)
report_query_parameters=[{'name':name,'in':'query','required':False,'schema':entry['schemas'][0]if len(entry['schemas'])==1 else {'anyOf':entry['schemas']},'description':'Only valid for the selected report: '+', '.join(entry['reports'])+'. The v_ prefix in the path is optional. Filter operators use the resource contract; sort permits only that report\'s readable fields.'} for name,entry in report_parameters.items()]


async def report_rows(conn,principal,name,filters,limit):
    resource=name if name.startswith('v_') else 'v_'+name
    if resource not in manifest['resources'] or manifest['resources'][resource]['kind']!='view': raise ApiError(404,'unknown_report','Report not found')
    if not 1<=limit<=10000: raise ApiError(422,'invalid_limit','Report limit must be 1..10000')
    rows=[]
    for offset in range(0,limit,500):
        page=await read_resource(conn,principal,resource,filters=filters,limit=min(500,limit-offset),offset=offset)
        rows.extend(page['items'])
        if not page['has_more']: break
    return rows


@router.get('/{name}',responses={200:{'model':ReportResponse}},openapi_extra={'parameters':report_query_parameters},description='Select a published view by name, with optional v_ prefix. Each declared filter lists exactly which reports support it; using a field outside the selected report fails validation. Response rows use the selected view DTO. At most twelve filters and three sort fields.')
async def report(name:str,request:Request,limit:int=Query(100,ge=1,le=500),principal=Depends(require_principal)):
    filters={k:v for k,v in request.query_params.items() if k!='limit'}
    async with transaction(principal,request.state.request_id,readonly=True) as conn:
        return {'items':json_value(await report_rows(conn,principal,name,filters,limit)),'limit':limit}
