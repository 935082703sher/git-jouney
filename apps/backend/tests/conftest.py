import os
from pathlib import Path
import sys
import pytest
from sqlalchemy import text,select
from sqlalchemy.engine import make_url

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DATABASE_URL']=os.environ.get('TEST_DATABASE_URL','postgresql+psycopg://aho:aho@localhost:5432/aho_test')
if not make_url(os.environ['DATABASE_URL']).database.endswith('_test'):
    raise RuntimeError('Tests require a dedicated database ending in _test')
os.environ['JWT_SECRET']='test-only-signing-key-that-is-at-least-32-characters'
os.environ['ENVIRONMENT']='testing'
os.environ['TELEGRAM_BOT_TOKEN']=''
os.environ['AHO_TELEGRAM_CHAT_ID']=''
from aho.db import Base,engine,Session
from aho.models import *
from aho.security import hash_password

@pytest.fixture(scope='session',autouse=True)
def schema():
    from alembic.config import Config
    from alembic import command
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    config.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'migrations'))
    command.upgrade(config, 'head')
    yield

@pytest.fixture
def world():
    with engine.begin() as c:
        tables=', '.join('"'+t.name+'"' for t in Base.metadata.sorted_tables)
        c.execute(text('TRUNCATE '+tables+' RESTART IDENTITY CASCADE'))
    with Session.begin() as db:
        roles={name:Role(name=name) for name in ['EMPLOYEE','AHO_SPECIALIST','AHO_MANAGER','ADMIN']};db.add_all(roles.values())
        dep=Department(name='IT'); cat=Category(name='Компьютеры'); other=Category(name='Электрика');db.add_all([dep,cat,other]);db.flush()
        users={}
        for i,(key,role) in enumerate([('employee','EMPLOYEE'),('employee2','EMPLOYEE'),('akmal','AHO_SPECIALIST'),('javohir','AHO_SPECIALIST'),('manager','AHO_MANAGER'),('admin','ADMIN')]):
            u=User(first_name=key.capitalize(),last_name='Тестов',email=key+'@example.local',password_hash=hash_password('Test-password-123!'),department_id=dep.id,mobile_phone='+998901234567',internal_phone='1234',status='ACTIVE',telegram_user_id=1000+i,telegram_chat_id=1000+i)
            u.roles=[roles[role]];db.add(u);db.flush();users[key]=u.id
            if role!='EMPLOYEE':
                r=Receiver(id=u.id,receiver_status='ACTIVE',connection_status='CONNECTED',is_fallback=role=='AHO_MANAGER');db.add(r);db.flush();r.categories=[cat]
        for p,resp,res in [('LOW',240,4320),('NORMAL',120,1440),('HIGH',30,480),('CRITICAL',10,120)]: db.add(SlaRule(priority=p,response_minutes=resp,resolution_minutes=res))
        for k,v in {'routing_mode':'CATEGORY','registration_mode':'ADMIN_APPROVAL','busy_receives':False,'auto_close_hours':72,'description_max':2000,'cancel_direct_statuses':['NEW']}.items():db.add(SystemSetting(key=k,value=v))
        return {**users,'category':cat.id,'other_category':other.id,'department':dep.id}

@pytest.fixture
def client(world):
    from fastapi.testclient import TestClient
    from aho.api import app
    with TestClient(app) as c: yield c

def login(client,who='admin'):
    r=client.post('/api/auth/login',json={'email':who+'@example.local','password':'Test-password-123!'})
    assert r.status_code==200,r.text
    client.headers['X-CSRF-Token']=r.json()['csrf']
    return r.json()
