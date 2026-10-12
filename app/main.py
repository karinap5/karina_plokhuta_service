import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from app.db import DB, metrics
from app.rules import fits, cancelled_share, utilization

app = FastAPI(title='karina_plokhuta — запись к специалисту', version='1.0.0')
security = HTTPBasic(auto_error=False)
BASE = Path(__file__).parent
app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')

@app.middleware('http')
async def timing(request: Request, call_next):
    m = {'db_ms': 0.0, 'queries': 0, 'connect_ms': 0.0, 'transaction_ms': 0.0}
    token = metrics.set(m)
    start = time.perf_counter()
    try:
        response = await call_next(request)
        total = (time.perf_counter() - start) * 1000
        response.headers['Cache-Control'] = 'no-store'
        for key, value in {'X-Server-Ms': total, 'X-DB-Ms': m['db_ms'],
                           'X-Connect-Ms': m['connect_ms'], 'X-Transaction-Ms': m['transaction_ms'],
                           'X-App-Ms': max(0, total-m['db_ms']), 'X-Query-Count': m['queries']}.items():
            response.headers[key] = str(round(value, 4))
        return response
    finally:
        metrics.reset(token)

def user(credentials: Annotated[HTTPBasicCredentials | None, Depends(security)]):
    if credentials is None:
        raise HTTPException(401, 'Требуется вход', headers={'WWW-Authenticate': 'Basic'})
    with DB() as db:
        rows = db.query('SELECT id, username, salt, password_hash FROM accounts WHERE username=%s', (credentials.username,))
    if rows:
        expected = hashlib.pbkdf2_hmac('sha256', credentials.password.encode(), bytes.fromhex(rows[0]['salt']), 100000).hex()
        if hmac.compare_digest(expected, rows[0]['password_hash']):
            return {'id': rows[0]['id'], 'username': rows[0]['username']}
    raise HTTPException(401, 'Неверный логин или пароль', headers={'WWW-Authenticate': 'Basic'})

Auth = Annotated[dict, Depends(user)]

def period(start: datetime, end: datetime):
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise HTTPException(422, 'Нужен период start < end с часовым поясом')
    return start, end

@app.get('/', include_in_schema=False)
def index():
    return FileResponse(BASE / 'static' / 'index.html')

@app.get('/health', include_in_schema=False)
def health():
    with DB() as db:
        db.query('SELECT 1')
    return {'status': 'ok', 'project': 'karina_plokhuta'}

@app.get('/api/auth')
def auth(me: Auth):
    return me

@app.get('/api/services')
def services(me: Auth):
    with DB() as db:
        return {'items': db.query('SELECT id, name, duration_minutes FROM services ORDER BY id')}

@app.get('/api/specialists')
def specialists(me: Auth, service_id: int = Query(..., ge=1)):
    with DB() as db:
        if not db.query('SELECT id FROM services WHERE id=%s', (service_id,)):
            raise HTTPException(404, 'Услуга не найдена')
        return {'items': db.query('SELECT id, name, service_id FROM specialists WHERE service_id=%s ORDER BY id', (service_id,))}

@app.get('/api/appointments')
def appointments(me: Auth, page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                 status: str | None = Query(None, pattern='^(booked|cancelled)$'),
                 specialist_id: int | None = Query(None, ge=1)):
    filters, params = ['a.account_id=%s'], [me['id']]
    if status:
        filters.append('a.status=%s'); params.append(status)
    if specialist_id:
        filters.append('a.specialist_id=%s'); params.append(specialist_id)
    where = ' AND '.join(filters)
    with DB() as db:
        total = db.query(f'SELECT count(*) AS n FROM appointments a WHERE {where}', params)[0]['n']
        items = db.query(f'''SELECT a.id,a.status,a.starts_at,a.ends_at,a.slot_id,a.specialist_id,a.service_id,
              s.name AS specialist,v.name AS service FROM appointments a
              JOIN specialists s ON s.id=a.specialist_id JOIN services v ON v.id=a.service_id
              WHERE {where} ORDER BY a.id DESC LIMIT %s OFFSET %s''', params + [size, (page-1)*size])
    return {'items': items, 'total': total, 'page': page, 'size': size}

@app.get('/api/appointments/{appointment_id}')
def detail(appointment_id: int, me: Auth):
    with DB() as db:
        rows = db.query('''SELECT a.*, s.name AS specialist,v.name AS service,v.duration_minutes,
             sl.starts_at AS slot_starts_at,sl.ends_at AS slot_ends_at
             FROM appointments a JOIN specialists s ON s.id=a.specialist_id
             JOIN services v ON v.id=a.service_id JOIN slots sl ON sl.id=a.slot_id
             WHERE a.id=%s AND a.account_id=%s''', (appointment_id,me['id']))
    if not rows:
        raise HTTPException(404, 'Запись не найдена')
    return rows[0]

@app.get('/api/slots')
def slots(me: Auth, service_id: int = Query(..., ge=1), start: datetime = Query(...), end: datetime = Query(...),
          specialist_id: int | None = Query(None, ge=1), page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100)):
    period(start, end)
    with DB() as db:
        found = db.query('SELECT duration_minutes FROM services WHERE id=%s',(service_id,))
        if not found:
            raise HTTPException(404, 'Услуга не найдена')
        minutes = found[0]['duration_minutes']
        condition = '''s.service_id=%s AND sl.starts_at >= %s AND sl.starts_at < %s
          AND sl.ends_at-sl.starts_at >= make_interval(mins => %s)
          AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.specialist_id=sl.specialist_id
            AND a.status='booked' AND a.starts_at < sl.starts_at+make_interval(mins => %s) AND a.ends_at > sl.starts_at)'''
        params = [service_id,start,end,minutes,minutes]
        if specialist_id:
            condition += ' AND sl.specialist_id=%s'; params.append(specialist_id)
        total = db.query(f'SELECT count(*) AS n FROM slots sl JOIN specialists s ON s.id=sl.specialist_id WHERE {condition}',params)[0]['n']
        items = db.query(f'''SELECT sl.id,sl.specialist_id,s.name AS specialist,sl.starts_at,sl.ends_at
           FROM slots sl JOIN specialists s ON s.id=sl.specialist_id WHERE {condition}
           ORDER BY sl.starts_at,sl.id LIMIT %s OFFSET %s''',params+[size,(page-1)*size])
    return {'items':items,'total':total,'page':page,'size':size}

class Booking(BaseModel):
    model_config = ConfigDict(extra='forbid')
    slot_id: int = Field(ge=1)
    service_id: int = Field(ge=1)

@app.post('/api/appointments', status_code=201)
def book(body: Booking, me: Auth):
    with DB() as db:
        rows = db.query('SELECT * FROM slots WHERE id=%s',(body.slot_id,))
        services = db.query('SELECT * FROM services WHERE id=%s',(body.service_id,))
        if not rows or not services:
            raise HTTPException(404, 'Слот или услуга не найдены')
        slot, svc = rows[0], services[0]
        specialist = db.query('SELECT * FROM specialists WHERE id=%s FOR UPDATE',(slot['specialist_id'],))[0]
        if specialist['service_id'] != body.service_id or not fits(slot['starts_at'], svc['duration_minutes'],slot['starts_at'],slot['ends_at']):
            raise HTTPException(409,'Услуга не подходит этому слоту')
        end = slot['starts_at']+timedelta(minutes=svc['duration_minutes'])
        if db.query("SELECT id FROM appointments WHERE specialist_id=%s AND status='booked' AND starts_at < %s AND ends_at > %s",(slot['specialist_id'],end,slot['starts_at'])):
            raise HTTPException(409,'Время уже занято')
        return db.query('''INSERT INTO appointments(account_id,slot_id,specialist_id,service_id,starts_at,ends_at,status)
              VALUES (%s,%s,%s,%s,%s,%s,'booked') RETURNING *''',
              (me['id'],body.slot_id,slot['specialist_id'],body.service_id,slot['starts_at'],end))[0]

@app.post('/api/appointments/{appointment_id}/cancel')
def cancel(appointment_id: int, me: Auth):
    with DB() as db:
        rows = db.query('SELECT * FROM appointments WHERE id=%s AND account_id=%s',(appointment_id,me['id']))
        if not rows:
            raise HTTPException(404,'Запись не найдена')
        db.query('SELECT id FROM specialists WHERE id=%s FOR UPDATE',(rows[0]['specialist_id'],))
        return db.query("UPDATE appointments SET status='cancelled',cancelled_at=COALESCE(cancelled_at,now()) WHERE id=%s RETURNING *",(appointment_id,))[0]

@app.get('/api/summary')
def summary(me: Auth, start: datetime = Query(...), end: datetime = Query(...)):
    period(start,end)
    with DB() as db:
        counts = db.query('''SELECT count(*) AS total,count(*) FILTER (WHERE status='cancelled') AS cancelled
           FROM appointments WHERE starts_at >= %s AND starts_at < %s''',(start,end))[0]
        items = db.query('''SELECT s.id,s.name,
           COALESCE(sl.minutes,0) AS available_minutes,COALESCE(a.minutes,0) AS booked_minutes
           FROM specialists s LEFT JOIN (
             SELECT specialist_id,sum(extract(epoch FROM (least(ends_at,%s)-greatest(starts_at,%s)))/60) AS minutes
             FROM slots WHERE starts_at < %s AND ends_at > %s GROUP BY specialist_id
           ) sl ON sl.specialist_id=s.id LEFT JOIN (
             SELECT specialist_id,sum(extract(epoch FROM (least(ends_at,%s)-greatest(starts_at,%s)))/60) AS minutes
             FROM appointments WHERE status='booked' AND starts_at < %s AND ends_at > %s GROUP BY specialist_id
           ) a ON a.specialist_id=s.id ORDER BY s.id''',(end,start,end,start,end,start,end,start))
    for row in items:
        row['utilization_percent'] = utilization(row['booked_minutes'],row['available_minutes'])
    return {'total':counts['total'],'cancelled':counts['cancelled'],
            'cancelled_percent':cancelled_share(counts['cancelled'],counts['total']),
            'start':start,'end':end,'items':items}
