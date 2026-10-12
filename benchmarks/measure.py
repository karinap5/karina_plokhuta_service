"""Serial authenticated measurements, with all raw samples saved."""
import argparse,json,math,platform,statistics,time
from datetime import datetime,timezone
from pathlib import Path
import httpx
from app.setup_db import seed
from app.db import DB
P={'start':'2026-10-01T00:00:00Z','end':'2027-02-04T00:00:00Z'}
def percentiles(values):
    values=sorted(values)
    return {'p50':statistics.median(values),'p95':values[math.ceil(.95*len(values))-1],'max':max(values)}
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('volume',choices=['small','working'])
    parser.add_argument('--url',default='http://127.0.0.1:20803')
    parser.add_argument('--repeats',type=int,default=20)
    parser.add_argument('--warmup',type=int,default=1)
    args=parser.parse_args()
    if args.repeats<20 or args.warmup<1: parser.error('At least 20 repeats and one warmup required')
    seed(args.volume)
    with DB() as db:
        before={t:db.query(f'SELECT count(*) AS n FROM {t}')[0]['n'] for t in ['accounts','services','specialists','slots','appointments']}
        db_version=db.query('SELECT version() AS version')[0]['version']
    result={'volume':args.volume,'account':'demo','warmup':args.warmup,'repeats':args.repeats,
            'time_utc':datetime.now(timezone.utc).isoformat(),'platform':platform.platform(),
            'python':platform.python_version(),'postgres':db_version,'counts_before':before,'operations':{}}
    with httpx.Client(base_url=args.url,auth=('demo','demo'),timeout=120) as c:
        def series(name,method,path,params=None,bodies=None):
            rows=[]
            for i in range(args.warmup+args.repeats):
                actual_path=path(i) if callable(path) else path
                start=time.perf_counter()
                response=c.request(method,actual_path,params=params,json=(bodies(i) if bodies else None))
                elapsed=(time.perf_counter()-start)*1000
                response.raise_for_status()
                d=response.json()
                row={'client_ms':elapsed,'server_ms':float(response.headers['x-server-ms']),
                     'db_ms':float(response.headers['x-db-ms']),'app_ms':float(response.headers['x-app-ms']),
                     'connect_ms':float(response.headers['x-connect-ms']),'transaction_ms':float(response.headers['x-transaction-ms']),
                     'queries':int(response.headers['x-query-count']),'bytes':len(response.content),'status':response.status_code}
                if i>=args.warmup: rows.append(row)
                if name=='create': created.append(d['id'])
            summary={key:percentiles([r[key] for r in rows]) for key in ['client_ms','server_ms','db_ms','app_ms','connect_ms','transaction_ms']}
            summary['queries']=sorted(set(r['queries'] for r in rows))
            result['operations'][name]={'method':method,'path':path if not callable(path) else '/api/appointments/{id}/cancel',
                                        'params':params,'samples':rows,'summary':summary}
            print(name,round(summary['client_ms']['p50'],2),round(summary['client_ms']['p95'],2),summary['queries'],flush=True)
        series('auth','GET','/api/auth')
        series('services','GET','/api/services')
        series('specialists','GET','/api/specialists',{'service_id':1})
        series('list','GET','/api/appointments',{'page':1,'size':20,'status':'booked','specialist_id':1})
        series('detail','GET','/api/appointments/1')
        series('slots','GET','/api/slots',{**P,'service_id':1,'size':20})
        series('summary','GET','/api/summary',P)
        with DB() as db:
            free=db.query("SELECT sl.id FROM slots sl JOIN specialists s ON s.id=sl.specialist_id WHERE s.service_id=1 AND NOT EXISTS(SELECT 1 FROM appointments a WHERE a.slot_id=sl.id AND a.status='booked') ORDER BY sl.id DESC LIMIT %s",(args.repeats+args.warmup,))
        if len(free)<args.repeats+args.warmup: raise RuntimeError('Not enough free slots')
        created=[]
        series('create','POST','/api/appointments',bodies=lambda i:{'slot_id':free[i]['id'],'service_id':1})
        series('cancel','POST',lambda i:f'/api/appointments/{created[i]}/cancel')
    with DB() as db:
        result['counts_after']={t:db.query(f'SELECT count(*) AS n FROM {t}')[0]['n'] for t in before}
        result['indexes']=db.query('SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema() ORDER BY tablename,indexname')
    (Path(__file__).parent/(args.volume+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
