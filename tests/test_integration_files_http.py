"""Actual API/Procrastinate/S3/ClamAV/Qdrant checks; BGE is a protocol fixture."""
import asyncio
import hashlib
import io
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import zipfile
import httpx
from test_integration_support import settings, output_directory


def pdf_bytes():
    stream = b'BT /F1 12 Tf 72 720 Td (Verified PDF text.) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    data = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(str(index).encode()+b' 0 obj\n'+value+b'\nendobj\n')
    start = len(data)
    data.extend(b'xref\n0 6\n0000000000 65535 f \n')
    for offset in offsets[1:]:
        data.extend(f'{offset:010d} 00000 n \n'.encode())
    data.extend(b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(start).encode()+b'\n%%EOF\n')
    return bytes(data)


def docx_bytes():
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        for name, text in [('document', 'Verified DOCX text.'), ('header1', 'Verified header.'), ('footer1', 'Verified footer.')]:
            archive.writestr('word/'+name+'.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:document>')
    return raw.getvalue()


async def await_job(client, job_id, seconds=90):
    for _ in range(seconds*2):
        response = await client.get('/api/v1/jobs/'+str(job_id))
        assert response.status_code == 200, response.text
        job = response.json()
        if job['status'] in ('succeeded', 'failed'):
            return job
        await asyncio.sleep(.5)
    raise AssertionError('Job did not finish within bounded integration deadline')


async def main():
    assert os.environ.get('RUN_LINUX_WORKER_TESTS') == '1', 'Explicit Linux worker/service mutation lease required'
    root = Path(__file__).resolve().parents[1]
    container=os.environ['VERIFIED_WORKER_CONTAINER_ID'];expected_image=os.environ['VERIFIED_WORKER_IMAGE_ID']
    image=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .Image}}',container],text=True))
    running=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State.Running}}',container],text=True))
    assert image==expected_image and running,'Expected frozen Linux worker must be running'
    env = settings()
    output = output_directory(env)
    path=output/('linux-files-http-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    evidence={'worker':{'container':container,'image':image},'cases':[]}
    def record(case,expected,actual):
        evidence['cases'].append({'id':case,'expected':expected,'actual':actual,'status':'pass'})
        path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
    async with httpx.AsyncClient(base_url=env.get('BASE_URL', 'http://127.0.0.1:8000'), trust_env=False, timeout=30,
                                 headers={'Origin': 'http://localhost:5173'}) as client:
        response = await client.post('/api/v1/auth/login', json={'login': 'admin', 'password': env['BOOTSTRAP_PASSWORD']})
        assert response.status_code == 200, response.text
        client.headers['Authorization'] = 'Bearer '+response.json()['access_token']
        uploads = []
        values = [('source.txt', 'text/plain', b'Unique31415 provenance regulation original.'),
                  ('source.pdf', 'application/pdf', pdf_bytes()),
                  ('source.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', docx_bytes())]
        for filename, mime, raw in values:
            key = 'file-test-'+str(uuid4())
            response = await client.post('/api/v1/files', files={'file': (filename, raw, mime)},
                data={'title': filename, 'source': 'urn:verification:'+key, 'purpose': 'regulation'}, headers={'Idempotency-Key': key})
            assert response.status_code == 202, (response.status_code, response.text)
            value = response.json()
            job = await await_job(client, value['job']['id'])
            assert job['status'] == 'succeeded', job
            metadata = (await client.get('/api/v1/files/'+value['file_id'])).json()
            version = metadata['versions'][0]
            assert version['state'] == 'ready' and version['sha256'] == hashlib.sha256(raw).hexdigest()
            assert version['quality']['page_numbers'] == filename.endswith('.pdf')
            download = await client.get(f"/api/v1/files/{value['file_id']}/versions/{value['version_id']}/download")
            assert download.status_code == 200 and download.content == raw
            duplicate = await client.post('/api/v1/files', files={'file': (filename, raw, mime)},
                data={'title': 'changed metadata', 'source': 'urn:verification:'+key, 'purpose': 'regulation'}, headers={'Idempotency-Key': key})
            assert duplicate.status_code == 409, duplicate.text
            identical=await client.post('/api/v1/files',files={'file':(filename,raw,mime)},data={'title':filename,'source':'urn:verification:'+key,'purpose':'regulation'},headers={'Idempotency-Key':key})
            assert identical.status_code==202 and identical.json()['file_id']==value['file_id'] and identical.json()['version_id']==value['version_id'] and identical.json()['job']['id']==value['job']['id']
            record('LINUX-FILE-'+filename,{'state':'ready','sha256':hashlib.sha256(raw).hexdigest(),'same_key_same_ids':True,'different_metadata':409},
                {'upload':value,'job':job,'metadata':metadata,'download':{'status':download.status_code,'bytes':len(download.content),'sha256':hashlib.sha256(download.content).hexdigest()},'identical_retry':identical.json(),'changed_metadata':{'status':duplicate.status_code,'body':duplicate.json()}})
            uploads.append((value, raw))
            print('PASS real upload/scan/parse/index/download and idempotency metadata:', filename, flush=True)
        first, old_bytes = uploads[0]
        new_bytes = b'Unique31415 provenance regulation revised.'
        response = await client.post(f"/api/v1/files/{first['file_id']}/versions", files={'file': ('source.txt', new_bytes, 'text/plain')},
            headers={'Idempotency-Key': 'version-'+str(uuid4())})
        assert response.status_code == 202, response.text
        version = response.json()
        assert (await await_job(client, version['job']['id']))['status'] == 'succeeded'
        assert (await client.get(f"/api/v1/files/{first['file_id']}/versions/{first['version_id']}/download")).content == old_bytes
        assert (await client.get(f"/api/v1/files/{first['file_id']}/versions/{version['version_id']}/download")).content == new_bytes
        record('LINUX-IMMUTABLE-VERSIONS',{'old_sha256':hashlib.sha256(old_bytes).hexdigest(),'new_sha256':hashlib.sha256(new_bytes).hexdigest()},{'old':first,'new':version,'original_bytes_preserved':True})
        print('PASS immutable old/new originals', flush=True)
        marker = 'VERIFY_TOOL:'+json.dumps({'name': 'search_regulations', 'arguments': {'query': 'Unique31415 provenance regulation revised'}})
        search = await client.post('/api/v1/agent/chat', json={'message': marker})
        assert search.status_code == 200, search.text
        matches = json.loads(json.loads(search.json()['answer'])['tool_results'][0])
        assert any(match.get('version_id') == version['version_id'] for match in matches), matches
        assert not any(match.get('version_id') == first['version_id'] for match in matches)
        record('LINUX-ORIGINAL-SEARCH-READY-VERSION',{'present_version':version['version_id'],'absent_old_version':first['version_id']},{'http_status':search.status_code,'original_response':search.json(),'matches':matches})
        print('PASS actual original search tool + real Qdrant RRF + latest ready-version DB recheck', flush=True)
        eicar = b'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'
        infected = await client.post('/api/v1/files', files={'file': ('eicar.txt', eicar, 'text/plain')},
            data={'title': 'scanner verification'}, headers={'Idempotency-Key': 'eicar-'+str(uuid4())})
        assert infected.status_code == 202, infected.text
        infected = infected.json()
        failure = await await_job(client, infected['job']['id'])
        assert failure['status'] == 'failed' and failure['error_code'] == 'malware_detected', failure
        denied = await client.get(f"/api/v1/files/{infected['file_id']}/versions/{infected['version_id']}/download")
        assert denied.status_code == 409
        record('LINUX-CLAMAV-EICAR-FAILCLOSED',{'job':'failed','code':'malware_detected','download':409},{'upload':infected,'job':failure,'download':{'status':denied.status_code,'body':denied.json()}})
        print('PASS real ClamAV EICAR rejection + quarantined download denial', flush=True)
        export = await client.post('/api/v1/exports', json={'report': 'students', 'limit': 3}, headers={'Idempotency-Key': 'export-'+str(uuid4())})
        assert export.status_code == 202, export.text
        job = await await_job(client, export.json()['id'])
        assert job['status'] == 'succeeded', job
        download = await client.get(f"/api/v1/files/{job['result']['file_id']}/versions/{job['result']['version_id']}/download")
        assert download.status_code == 200 and download.content.startswith(b'\xef\xbb\xbf')
        record('LINUX-REPORT-EXPORT',{'job':'succeeded','download':200,'utf8_bom':True},{'enqueue':export.json(),'job':job,'download':{'status':download.status_code,'bytes':len(download.content),'sha256':hashlib.sha256(download.content).hexdigest(),'utf8_bom':download.content.startswith(b'\xef\xbb\xbf')}})
        print('PASS actual worker report export through shared core rows + private S3 + scanner', flush=True)
        print('Evidence',path,flush=True)


if __name__ == '__main__':
    asyncio.run(main())
