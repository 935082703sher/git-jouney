from datetime import timedelta
from aiogram.fsm.storage.base import BaseStorage, StorageKey, DefaultKeyBuilder
from sqlalchemy.dialects.postgresql import insert
from .db import Session, now
from .models import BotState
from .services import setting

class PostgresStorage(BaseStorage):
    def key(self,key: StorageKey): return DefaultKeyBuilder(with_bot_id=True).build(key,'fsm')
    def read(self,key):
        with Session.begin() as db:
            row=db.get(BotState,self.key(key))
            if not row: return None,{}
            if row.updated_at<now()-timedelta(minutes=setting(db,'fsm_timeout_minutes',60)):
                db.delete(row); return None,{}
            return row.state,dict(row.data)
    async def set_state(self,key,state=None):
        value=state.state if hasattr(state,'state') else state
        with Session.begin() as db:
            db.execute(insert(BotState).values(key=self.key(key),state=value,data={}).on_conflict_do_update(index_elements=['key'],set_={'state':value,'updated_at':now()}))
    async def get_state(self,key): return self.read(key)[0]
    async def set_data(self,key,data):
        with Session.begin() as db:
            db.execute(insert(BotState).values(key=self.key(key),data=data).on_conflict_do_update(index_elements=['key'],set_={'data':data,'updated_at':now()}))
    async def get_data(self,key): return self.read(key)[1]
    async def close(self): pass
