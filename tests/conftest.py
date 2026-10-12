import os
os.environ['DB_SCHEMA']='karina_plokhuta_test'
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.setup_db import seed
@pytest.fixture
def client():
    seed('small')
    with TestClient(app) as c: yield c
@pytest.fixture
def auth(): return ('demo','demo')
