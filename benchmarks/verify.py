"""Offline audit of saved baseline samples against statistics and measured sources."""
import hashlib
import json
import math
import statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
B=ROOT/'benchmarks'
def main():
    metadata=json.loads((B/'run_metadata.json').read_text())
    for name,digest in metadata['source_sha256'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest, name
    expected={'auth':1,'services':2,'specialists':3,'list':3,'detail':2,'slots':4,'summary':3,'create':6,'cancel':4}
    result={'project':'karina_plokhuta','measured_server_sources_unchanged':True,'volumes':{}}
    for volume,n in [('small',300),('working',50000)]:
        data=json.loads((B/f'{volume}.json').read_text())
        assert data['counts_before']['appointments']==n
        assert data['counts_after']['appointments']==n+data['warmup']+data['repeats']
        assert data['warmup']>=1 and data['repeats']>=20 and data['account']=='demo'
        assert set(data['operations'])==set(expected)
        for op,d in data['operations'].items():
            rows=d['samples']; assert len(rows)==data['repeats']
            for row in rows:
                assert row['status']==(201 if op=='create' else 200)
                assert row['queries']==expected[op]
                assert abs(row['server_ms']-row['db_ms']-row['app_ms'])<.001
            for metric,summary in d['summary'].items():
                if metric=='queries':continue
                values=sorted(r[metric] for r in rows)
                recomputed={'p50':statistics.median(values),'p95':values[math.ceil(.95*len(values))-1],'max':max(values)}
                assert summary==recomputed,(volume,op,metric)
        result['volumes'][volume]={'operations':9,'samples':sum(len(d['samples']) for d in data['operations'].values()),'statistics_recomputed':True,'statuses_and_query_counts_valid':True}
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
