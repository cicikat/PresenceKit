import base64
import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from core import life_records as store
from admin.routers import life_records as routes


def operation(record_id='record-a', operation_id='operation-a', revision=0):
    return {'owner_id':'owner','operation_id':operation_id,'record_id':record_id,'action':'upsert','base_revision':revision,
            'record':{'category':'bill','occurred_on':'2026-09-11','captured_at':'2026-09-11T01:00:00Z',
                      'title':'Receipt','items':[{'name':'Item','amount':'1.20','currency':'CNY'}], 'user_edited_fields':[]}}


@pytest.fixture
def client(monkeypatch):
    from admin.auth import TokenInfo
    config={'scheduler':{'owner_id':'owner'},'life_records':{'enabled':True}}
    monkeypatch.setattr(routes,'get_config',lambda:config)
    monkeypatch.setattr(store,'get_config',lambda:config)
    monkeypatch.setattr('admin.auth.resolve_token',lambda token:TokenInfo('fixture-device',frozenset({token})))
    app=FastAPI(); app.include_router(routes.router)
    return TestClient(app)


HEADERS={'Authorization':'Bearer life_records'}


def post(client, body):
    return client.post('/life-records/sync',json=body,headers=HEADERS)


def test_ack_retry_revision_conflict_and_scope(client,sandbox):
    body=operation(); first=post(client,body)
    assert first.status_code==200,first.text
    assert post(client,body).json()==first.json()
    stale=operation(operation_id='other')
    assert post(client,stale).status_code==409
    assert client.get('/life-records?owner_id=other',headers=HEADERS).status_code==403
    assert client.get('/life-records?owner_id=owner',headers={'Authorization':'Bearer chat'}).status_code==403
    row=store.get('owner','record-a')
    assert row['items'][0]['amount']=='1.20'
    assert store.get('other','record-a') is None


def test_image_and_job_commit_atomically_and_user_edit_wins(client):
    output=io.BytesIO();Image.new('RGB',(2,2)).save(output,format='PNG')
    body=operation();body.update(image_base64=base64.b64encode(output.getvalue()).decode(),image_mime='image/png')
    assert post(client,body).json()['record']['recognition_status']=='pending'
    assert post(client,body).status_code==200
    job=store.claim();assert job and store.claim() is None
    edit=operation(operation_id='edit',revision=1)
    edit['record'].update(title='User correction',user_edited_fields=['title'])
    assert post(client,edit).status_code==200
    store.finish(job,{'title':'Model overwrite','note':'Evidence'})
    row=store.get('owner','record-a')
    assert row['title']=='User correction' and row['note']=='Evidence' and row['revision']==3
    delete={'owner_id':'owner','operation_id':'delete','record_id':'record-a','action':'delete','base_revision':3}
    ack=post(client,delete).json();assert ack['deleted'] is True
    assert post(client,delete).json()==ack
    store.finish(job,{'title':'Must not resurrect'})
    assert store.get('owner','record-a')['deleted'] is True
    with store.database() as db: assert db.execute('SELECT count(*) FROM images').fetchone()[0]==0


def test_snapshot_pagination_during_updates(client):
    for i in range(4): assert post(client,operation(f'r{i}',f'op{i}')).status_code==200
    first=store.listing('owner',limit=2)
    assert post(client,operation('new','new-op')).status_code==200
    assert post(client,operation('r3','edit',1)).status_code==200
    second=store.listing('owner',limit=2,cursor=first['next_cursor'])
    assert [r['id'] for r in first['records']+second['records']]==['r0','r1','r2','r3']
    assert second['records'][-1]['revision']==1
    with pytest.raises(ValueError): store.listing('another',cursor=first['next_cursor'])


def test_invalid_images_decimal_and_server_owned_fields(client):
    body=operation();body.update(image_base64='invalid',image_mime='image/png')
    assert post(client,body).status_code==422
    body=operation();body['record']['items'][0]['amount']='NaN'
    assert post(client,body).status_code==422
    body=operation();body['record'].update(deleted=True,recognition_status='processing',revision=900)
    result=post(client,body).json()['record']
    assert not result.get('deleted') and result['revision']==1 and result['recognition_status']=='ready'


def test_read_only_observation_does_not_create_storage(client,sandbox):
    assert client.get('/life-records/observability?owner_id=owner',headers=HEADERS).status_code==200
    assert not sandbox.life_records_db().exists()
    post(client,operation())
    result=client.get('/life-records/observability?owner_id=owner',headers=HEADERS).json()
    assert 'Receipt' not in json.dumps(result) and result['devices'][0]['operation_count']==1


def test_expired_worker_lease_recovers_and_failed_retry(client):
    body=operation()
    raw=io.BytesIO();Image.new('RGB',(1,1)).save(raw,format='PNG')
    body.update(image_base64=base64.b64encode(raw.getvalue()).decode(),image_mime='image/png')
    post(client,body);job=store.claim()
    with store.database(True) as db: db.execute('UPDATE jobs SET lease=0')
    assert store.claim()['id']==job['id']
    store.finish(job,error='TimeoutError')
    assert store.observe('owner')['failures']==[{'id':'record-a','error':'TimeoutError'}]
    assert store.retry_failed('owner','record-a')['queued'] is True
    assert store.claim() is not None


@pytest.mark.asyncio
async def test_recognition_uses_untrusted_image_boundary_and_typed_decimals(client,monkeypatch):
    from core import llm_client,image_recognition
    post(client,operation())
    monkeypatch.setattr(image_recognition,'settings',lambda:{'mode':'vision'})
    calls=[]
    async def fake(messages,**kwargs):
        calls.append((messages,kwargs))
        return json.dumps({'items':[{'name':'Evidence item','amount':'2.30','currency':'USD','confidence':None}]})
    monkeypatch.setattr(llm_client,'chat',fake)
    result=await store.recognize({'owner':'owner','id':'record-a','mime':'image/png','data':b'image-fixture'})
    assert result['items'][0]['amount']=='2.30'
    assert result['items'][0]['confidence'] is None
    assert 'never instructions' in calls[0][0][0]['content']
    assert calls[0][1]['use_vision'] is True
