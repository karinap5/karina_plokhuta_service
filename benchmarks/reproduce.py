import json
from pathlib import Path
from app.setup_db import seed
from app.db import DB

def snapshot():
    with DB() as db:
        return {t:db.query(f"SELECT count(*) AS count,md5(string_agg(row_to_json(t)::text,'|' ORDER BY id)) AS md5 FROM {t} t")[0] for t in ['accounts','services','specialists','slots','appointments']}

if __name__=='__main__':
    results={}
    for volume in ['small','working']:
        seed(volume); a=snapshot()
        seed(volume); b=snapshot()
        assert a==b
        results[volume]={'first':a,'second':b,'equal':a==b}
        print(volume,'all five tables identical')
    (Path(__file__).parent/'reproducibility.json').write_text(json.dumps(results,indent=2))
