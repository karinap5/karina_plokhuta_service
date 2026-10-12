import os
import re
import time
from contextvars import ContextVar
import psycopg
from psycopg.rows import dict_row

metrics = ContextVar('metrics', default=None)
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://app:changeme@localhost:25433/karina_plokhuta')
SCHEMA = os.getenv('DB_SCHEMA', 'karina_plokhuta')
if not re.fullmatch(r'[a-z][a-z0-9_]*', SCHEMA):
    raise ValueError('Invalid schema name')

class DB:
    def __enter__(self):
        start = time.perf_counter()
        self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row,
                                   options=f'-c search_path={SCHEMA},public')
        m = metrics.get()
        if m is not None:
            m['connect_ms'] += (time.perf_counter() - start) * 1000
        return self

    def query(self, sql, params=()):
        start = time.perf_counter()
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if cur.description else []
        finally:
            m = metrics.get()
            if m is not None:
                m['db_ms'] += (time.perf_counter() - start) * 1000
                m['queries'] += 1

    def __exit__(self, typ, value, traceback):
        start = time.perf_counter()
        try:
            self.conn.rollback() if typ else self.conn.commit()
        finally:
            self.conn.close()
            m = metrics.get()
            if m is not None:
                m['transaction_ms'] += (time.perf_counter() - start) * 1000
