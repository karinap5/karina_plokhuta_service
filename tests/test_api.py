from concurrent.futures import ThreadPoolExecutor
import pytest
from app.db import DB
P={'start':'2026-10-01T00:00:00Z','end':'2027-02-04T00:00:00Z'}
@pytest.mark.parametrize('path',['auth','services','specialists?service_id=1','appointments','appointments/1','slots?service_id=1&start=2026-10-01T00:00:00Z&end=2027-02-04T00:00:00Z','summary?start=2026-10-01T00:00:00Z&end=2027-02-04T00:00:00Z'])
def test_get_requires_authorization(client,path):
    assert client.get('/api/'+path).status_code==401
@pytest.mark.parametrize('path',['appointments','appointments/1/cancel'])
def test_post_requires_authorization(client,path):
    assert client.post('/api/'+path,json={'slot_id':401,'service_id':1}).status_code==401
def test_wrong_password(client):
    assert client.get('/api/auth',auth=('demo','bad')).status_code==401
def test_auth_and_services(client,auth):
    assert client.get('/api/auth',auth=auth).json()=={'id':1,'username':'demo'}
    data=client.get('/api/services',auth=auth).json()['items']
    assert len(data)==10 and set(data[0])=={'id','name','duration_minutes'}
def test_specialist_service_mapping(client,auth):
    r=client.get('/api/specialists',params={'service_id':1},auth=auth)
    assert r.status_code==200
    assert all(s['service_id']==1 for s in r.json()['items'])
def test_pagination_and_filter(client,auth):
    first=client.get('/api/appointments',params={'size':7,'status':'booked'},auth=auth).json()
    second=client.get('/api/appointments',params={'size':7,'status':'booked','page':2},auth=auth).json()
    assert len(first['items'])==len(second['items'])==7
    assert not {a['id'] for a in first['items']} & {a['id'] for a in second['items']}
    assert all(a['status']=='booked' for a in first['items'])
@pytest.mark.parametrize('params',[{'page':0},{'size':101},{'status':'broken'},{'specialist_id':0}])
def test_invalid_list_params(client,auth,params):
    assert client.get('/api/appointments',params=params,auth=auth).status_code==422
def test_detail_and_missing(client,auth):
    r=client.get('/api/appointments/1',auth=auth)
    assert r.status_code==200
    assert {'id','specialist','service','slot_starts_at','slot_ends_at'} <= r.json().keys()
    assert client.get('/api/appointments/999999',auth=auth).status_code==404
def test_invalid_period_and_missing_service(client,auth):
    assert client.get('/api/slots',params={**P,'service_id':999},auth=auth).status_code==404
    assert client.get('/api/slots',params={**P,'service_id':1,'end':P['start']},auth=auth).status_code==422
    assert client.get('/api/summary',params={'start':'2026-10-01','end':'2027-02-04'},auth=auth).status_code==422
def test_book_cancel_and_return_slot(client,auth):
    body={'slot_id':401,'service_id':1}
    r=client.post('/api/appointments',json=body,auth=auth)
    assert r.status_code==201
    aid=r.json()['id']
    assert client.post('/api/appointments',json=body,auth=auth).status_code==409
    assert client.post(f'/api/appointments/{aid}/cancel',auth=auth).json()['status']=='cancelled'
    assert client.post(f'/api/appointments/{aid}/cancel',auth=auth).status_code==200
    free=client.get('/api/slots',params={**P,'service_id':1,'size':100},auth=auth).json()['items']
    assert 401 in {s['id'] for s in free}
    assert client.post('/api/appointments',json=body,auth=auth).status_code==201
def test_wrong_service_short_slot_and_missing_slot(client,auth):
    assert client.post('/api/appointments',json={'slot_id':402,'service_id':1},auth=auth).status_code==409
    with DB() as db:
        db.query("UPDATE slots SET ends_at=starts_at+interval '10 minutes' WHERE id=403")
    assert client.post('/api/appointments',json={'slot_id':403,'service_id':3},auth=auth).status_code==409
    assert client.post('/api/appointments',json={'slot_id':999999,'service_id':1},auth=auth).status_code==404
    assert client.post('/api/appointments',json={'slot_id':0,'service_id':1},auth=auth).status_code==422
    assert client.post('/api/appointments/999999/cancel',auth=auth).status_code==404
def test_concurrent_booking_only_one_succeeds(client,auth):
    def book(_): return client.post('/api/appointments',json={'slot_id':411,'service_id':1},auth=auth).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(book,range(2)))
    assert sorted(statuses)==[201,409]
def test_overlapping_distinct_slots_rejected(client,auth):
    assert client.post('/api/appointments',json={'slot_id':411,'service_id':1},auth=auth).status_code==201
    with DB() as db:
        db.query("INSERT INTO slots SELECT 1001,specialist_id,starts_at+interval '10 minutes',ends_at+interval '10 minutes' FROM slots WHERE id=411")
    assert client.post('/api/appointments',json={'slot_id':1001,'service_id':1},auth=auth).status_code==409
def test_summary_matches_database_and_instrumentation(client,auth):
    r=client.get('/api/summary',params=P,auth=auth)
    assert r.status_code==200
    with DB() as db:
        counts=db.query("SELECT count(*) AS total,count(*) FILTER(WHERE status='cancelled') AS cancelled FROM appointments")[0]
    assert r.json()['total']==counts['total'] and r.json()['cancelled']==counts['cancelled']
    assert float(r.headers['x-db-ms'])>=0 and int(r.headers['x-query-count'])==3
    assert r.headers['cache-control']=='no-store'


def test_other_account_cannot_read_or_cancel(client,auth):
    with DB() as db:
        db.query("INSERT INTO accounts SELECT 2,'other',salt,password_hash FROM accounts WHERE id=1")
    other=('other','demo')
    assert client.get('/api/appointments/1',auth=other).status_code==404
    assert client.post('/api/appointments/1/cancel',auth=other).status_code==404
    assert client.get('/api/appointments',auth=other).json()['total']==0
    assert client.get('/api/appointments/1',auth=auth).json()['status']=='booked'

def test_summary_period_clipping_and_empty_period(client,auth):
    # First appointment of specialist 1 lasts 30 minutes; only 15 lie in this period.
    p={'start':'2026-10-01T08:15:00Z','end':'2026-10-01T08:45:00Z'}
    data=client.get('/api/summary',params=p,auth=auth).json()
    row=next(x for x in data['items'] if x['id']==1)
    assert row['available_minutes']==30
    assert row['booked_minutes']==15
    assert row['utilization_percent']==50
    assert data['total']==0  # appointments started before the period
    empty=client.get('/api/summary',params={'start':'2020-01-01T00:00:00Z','end':'2020-01-02T00:00:00Z'},auth=auth).json()
    assert empty['total']==empty['cancelled']==empty['cancelled_percent']==0
    assert all(x['utilization_percent']==0 for x in empty['items'])

def test_slots_half_open_period_and_short_duration(client,auth):
    p={'service_id':1,'start':'2026-10-06T08:00:00Z','end':'2026-10-06T09:00:00Z','size':100}
    data=client.get('/api/slots',params=p,auth=auth).json()
    assert [x['id'] for x in data['items']]==[401]
    with DB() as db:
        db.query("UPDATE slots SET ends_at=starts_at+interval '29 minutes' WHERE id=401")
    assert client.get('/api/slots',params=p,auth=auth).json()['items']==[]

def test_cancellation_timestamp_is_stable(client,auth):
    first=client.post('/api/appointments/1/cancel',auth=auth).json()
    second=client.post('/api/appointments/1/cancel',auth=auth).json()
    assert first['cancelled_at'] is not None
    assert first==second

@pytest.mark.parametrize('body',[{}, {'slot_id':401}, {'slot_id':401,'service_id':1,'unexpected':True}])
def test_booking_body_contract(client,auth,body):
    assert client.post('/api/appointments',json=body,auth=auth).status_code==422

def test_response_fields_match_contract(client,auth):
    appointment={'id','account_id','slot_id','specialist_id','service_id','starts_at','ends_at','status','cancelled_at'}
    detail=client.get('/api/appointments/1',auth=auth).json()
    assert set(detail)==appointment|{'specialist','service','duration_minutes','slot_starts_at','slot_ends_at'}
    page=client.get('/api/appointments',auth=auth).json()
    assert set(page)=={'items','total','page','size'}
    assert set(page['items'][0])=={'id','status','starts_at','ends_at','slot_id','specialist_id','service_id','specialist','service'}
    created=client.post('/api/appointments',json={'slot_id':401,'service_id':1},auth=auth)
    assert created.status_code==201 and set(created.json())==appointment
    assert set(client.post(f"/api/appointments/{created.json()['id']}/cancel",auth=auth).json())==appointment
    slots=client.get('/api/slots',params={**P,'service_id':1},auth=auth).json()
    assert set(slots)=={'items','total','page','size'}
    assert set(slots['items'][0])=={'id','specialist_id','specialist','starts_at','ends_at'}
    summary=client.get('/api/summary',params=P,auth=auth).json()
    assert set(summary)=={'total','cancelled','cancelled_percent','start','end','items'}
    assert set(summary['items'][0])=={'id','name','available_minutes','booked_minutes','utilization_percent'}
