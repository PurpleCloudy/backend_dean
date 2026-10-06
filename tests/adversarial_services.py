"""Real S3/Qdrant/ClamAV protocol probes with concrete effect observations."""
import asyncio
import hashlib
import json
import socket
import uuid
from datetime import datetime

import httpx
from aiobotocore.session import get_session
from botocore.config import Config
from botocore.exceptions import ClientError
from test_integration_support import settings, output_directory

OUT = output_directory(settings())


def record(case_id, request, expected, actual, assertions, evidence, role=None):
    result = {'case_id': case_id, 'request': request, 'expected': expected,
              'actual': actual, 'assertions': assertions,
              'status': 'pass' if all(assertions.values()) else 'fail'}
    if role:
        result['role'] = role
    with evidence.open('a', encoding='utf8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False) + '\n')
    return result


def scanner(command,data=None):
    with socket.create_connection(('127.0.0.1',53310),timeout=30) as conn:
        conn.sendall(command)
        if data is not None:
            conn.sendall(len(data).to_bytes(4,'big')+data+b'\0\0\0\0')
        result=bytearray()
        while not result.endswith(b'\0'):
            chunk=conn.recv(4096)
            if not chunk: break
            result.extend(chunk)
        return result.rstrip(b'\0').decode()


async def run():
    values=settings();OUT.mkdir(exist_ok=True)
    evidence=OUT/('real-services-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.jsonl')
    cases=[]
    version=await asyncio.to_thread(scanner,b'zVERSION\0')
    for name,data,expected in [('clean',b'Harmless deanery verification document.','stream: OK'),
        ('eicar',b'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*','FOUND')]:
        observed=await asyncio.to_thread(scanner,b'zINSTREAM\0',data)
        cases.append(record('CLAMAV-'+name,{'method':'INSTREAM','url':'tcp://127.0.0.1:53310','input_sha256':hashlib.sha256(data).hexdigest()},
            {'response':expected,'signature_database':'actual freshclam definitions, version must include database revision'},
            {'response':observed,'engine_database_version':version},
            {'scan_result':observed==expected if name=='clean' else observed.endswith('FOUND'),'actual_database':version.count('/')>=2},evidence))
    bucket='deanery-private'; key='verification/'+uuid.uuid4().hex; data='Синтетический исходный документ\n'.encode()
    endpoint='http://127.0.0.1:59000'
    session=get_session(); config=Config(s3={'addressing_style':'path'},retries={'max_attempts':0},connect_timeout=5,read_timeout=15)
    async with session.create_client('s3',endpoint_url=endpoint,region_name='us-east-1',aws_access_key_id=values['S3_ADMIN_ACCESS_KEY'],aws_secret_access_key=values['S3_ADMIN_SECRET_KEY'],config=config) as admin:
        try: await admin.head_bucket(Bucket=bucket)
        except ClientError as exc:
            if exc.response['ResponseMetadata']['HTTPStatusCode']!=404: raise
            await admin.create_bucket(Bucket=bucket)
    async with session.create_client('s3',endpoint_url=endpoint,region_name='us-east-1',aws_access_key_id=values['S3_ACCESS_KEY'],aws_secret_access_key=values['S3_SECRET_KEY'],config=config) as client:
        sha=hashlib.sha256(data).hexdigest()
        await client.put_object(Bucket=bucket,Key=key,Body=data,ContentType='text/plain',Metadata={'sha256':sha})
        try:
            response=await client.get_object(Bucket=bucket,Key=key)
            async with response['Body'] as stream: actual=await stream.read()
            listed=await client.list_objects_v2(Bucket=bucket,Prefix=key)
            cases.append(record('S3-AUTHENTICATED-ROUNDTRIP',{'method':'PUT/GET/HEAD/LIST','url':endpoint+'/'+bucket+'/'+key,'bytes':len(data),'sha256':sha},
                {'bytes':len(data),'sha256':sha,'mime':'text/plain','metadata':{'sha256':sha},'listed':1},
                {'bytes':len(actual),'sha256':hashlib.sha256(actual).hexdigest(),'mime':response['ContentType'],'metadata':response['Metadata'],'listed':listed.get('KeyCount')},
                {'exact_bytes':actual==data,'metadata':response['Metadata'].get('sha256')==sha,'list':listed.get('KeyCount')==1},evidence))
            async with httpx.AsyncClient(trust_env=False,timeout=15) as http:
                for method,path,payload in [('GET','/'+bucket+'?list-type=2',None),('GET','/'+bucket+'/'+key,None),('PUT','/'+bucket+'/unauthorized',b'forbidden'),('DELETE','/'+bucket+'/'+key,None)]:
                    r=await http.request(method,endpoint+path,content=payload)
                    cases.append(record('S3-ANONYMOUS-'+method+('-LIST' if '?' in path else ''),{'method':method,'url':endpoint+path},
                        {'status':403,'object_effect':'none'}, {'status':r.status_code,'body':r.text[:1000]}, {'access_denied':r.status_code==403},evidence,role='anonymous'))
        finally:
            await client.delete_object(Bucket=bucket,Key=key)
        absent=await client.list_objects_v2(Bucket=bucket,Prefix=key)
        cases.append(record('S3-DELETE',{'method':'DELETE/LIST','url':endpoint+'/'+bucket+'/'+key},{'key_count':0},{'key_count':absent.get('KeyCount')},{'removed':absent.get('KeyCount')==0},evidence))
    collection='verification_'+uuid.uuid4().hex
    base='http://127.0.0.1:56333/collections/'+collection
    async with httpx.AsyncClient(trust_env=False,timeout=15) as http:
        health=await http.get('http://127.0.0.1:56333/')
        vectors=(await http.post('http://127.0.0.1:58231/predict',json={'text':['verify document'],'return_dense':True,'return_sparse':True,'return_colbert':False})).json()
        dense=vectors['vector'][0]; sparse=vectors['sparse'][0]
        created=await http.put(base,json={'vectors':{'dense':{'size':1024,'distance':'Cosine'}},'sparse_vectors':{'sparse':{}}});created.raise_for_status()
        try:
            put=await http.put(base+'/points?wait=true',json={'points':[{'id':1,'vector':{'dense':dense,'sparse':{'indices':[int(k) for k in sparse],'values':list(sparse.values())}},'payload':{'source':'independent-real-service-probe'}}]});put.raise_for_status()
            result=await http.post(base+'/points/query',json={'prefetch':[{'query':dense,'using':'dense','limit':5},{'query':{'indices':[int(k) for k in sparse],'values':list(sparse.values())},'using':'sparse','limit':5}],'query':{'fusion':'rrf'},'limit':1,'with_payload':True});result.raise_for_status()
            ids=[point['id'] for point in result.json()['result']['points']]
            cases.append(record('QDRANT-BGE-RRF',{'method':'PUT/POST','url':base,'dense_dimensions':len(dense),'sparse_entries':len(sparse)},
                {'retrieved_ids':[1],'retrieval':'actual dense+sparse RRF; deterministic embeddings'}, {'retrieved_ids':ids,'qdrant':health.json()}, {'rrf_returns_inserted_point':ids==[1],'dimensions':len(dense)==1024},evidence))
        finally:
            deleted=await http.delete(base);deleted.raise_for_status()
    print(json.dumps({'cases':len(cases),'passed':sum(x['status']=='pass' for x in cases),'failed':sum(x['status']=='fail' for x in cases),'evidence':str(evidence)}))
    return all(c['status']=='pass' for c in cases)


if __name__=='__main__':
    raise SystemExit(0 if asyncio.run(run()) else 1)
