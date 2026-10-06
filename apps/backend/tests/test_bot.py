import itertools
import pytest
from datetime import datetime,timezone
from aiogram import Bot,Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import Update,Message as TgMessage,Chat,User as TgUser
from sqlalchemy import select,func
from aho.bot import router,DuplicateUpdateMiddleware,compact_id,expand_id
from aho.bot_storage import PostgresStorage
from aho.db import Session
from aho.models import User,Ticket,Role,BotState,Notification
from aho.services import action_ticket
from aho.schemas import Action

class FakeSession(BaseSession):
    def __init__(self):super().__init__();self.calls=[]
    async def close(self):pass
    async def stream_content(self,*args,**kwargs):
        yield b''
    async def make_request(self,bot,method,timeout=None):
        self.calls.append(method)
        if method.__api_method__=='getMe':return TgUser(id=123456789,is_bot=True,first_name='AHO',username='aho_test_bot')
        if method.__api_method__ in ('answerCallbackQuery','deleteWebhook'):return True
        return TgMessage(message_id=len(self.calls),date=datetime.now(timezone.utc),chat=Chat(id=int(getattr(method,'chat_id',98765)),type='private'),text=getattr(method,'text',''))

fake=FakeSession();bot=Bot('123456789:TEST_TOKEN_FOR_OFFLINE_HANDLER_TESTS',session=fake)
dp=Dispatcher(storage=PostgresStorage());dp.include_router(router);dp.update.outer_middleware(DuplicateUpdateMiddleware())
counter=itertools.count(50000)

def update(text,uid=98765,update_id=None):
    n=update_id or next(counter)
    return Update.model_validate({'update_id':n,'message':{'message_id':n,'date':int(datetime.now(timezone.utc).timestamp()),'chat':{'id':uid,'type':'private'},'from':{'id':uid,'is_bot':False,'first_name':'Test','username':'changed_username'},'text':text}})

@pytest.mark.asyncio
async def test_registration_ticket_wizard_persists_and_confirms(world):
    fake.calls.clear()
    for text in ['/start','Начать регистрацию','Иванов','Алексей','IT','1245','+998901234567']:
        await dp.feed_update(bot,update(text))
    with Session() as db:
        assert not db.scalar(select(User).where(User.telegram_user_id==98765))
        assert db.scalar(select(BotState)) is not None
    await dp.feed_update(bot,update('Подтвердить'))
    with Session.begin() as db:
        u=db.scalar(select(User).where(User.telegram_user_id==98765));assert u and u.status=='PENDING_APPROVAL';u.status='ACTIVE'
    for text in ['/start','➕ Создать заявку','Компьютеры','В кабинете 305 не работает компьютер','Главный офис','3','305','Пропустить','Обычная']:
        await dp.feed_update(bot,update(text))
    with Session() as db: assert db.scalar(select(func.count()).select_from(Ticket))==0
    confirm=update('Отправить заявку')
    await dp.feed_update(bot,confirm)
    await dp.feed_update(bot,confirm)
    with Session() as db:
        tickets=db.scalars(select(Ticket)).all();assert len(tickets)==1
        assert tickets[0].description=='В кабинете 305 не работает компьютер'
        assert db.scalar(select(Notification).where(Notification.ticket_id==tickets[0].id,Notification.event=='TICKET_NEW'))
    assert any('зарегистрирована' in (getattr(c,'text','') or '') for c in fake.calls)

@pytest.mark.asyncio
async def test_cancel_and_back_do_not_create_partial_records(world):
    for text in ['/start','Начать регистрацию','Иванов','Назад','Петров','Алексей','IT','1234','+998901234567']:
        await dp.feed_update(bot,update(text))
    with Session() as db:
        rows=list(db.scalars(select(BotState)))
        assert any(r.data.get('profile',{}).get('last_name')=='Петров' for r in rows)
    await dp.feed_update(bot,update('Отмена'))
    with Session() as db:assert not db.scalar(select(User).where(User.telegram_user_id==98765))

@pytest.mark.asyncio
async def test_stale_callback_authorization(world):
    from test_domain import make,act
    tid=make(world);act(world,tid,'akmal','accept')
    fake.calls.clear()
    n=next(counter)
    event=Update.model_validate({'update_id':n,'callback_query':{'id':str(n),'chat_instance':'1','from':{'id':1003,'is_bot':False,'first_name':'Javohir'},'data':f'act:accept:{tid}','message':{'message_id':1,'date':int(datetime.now(timezone.utc).timestamp()),'chat':{'id':1003,'type':'private'},'text':'Ticket'}}})
    await dp.feed_update(bot,event)
    assert any('уже принята' in (getattr(c,'text','') or '') for c in fake.calls)
    with Session() as db:assert db.get(Ticket,tid).assigned_to==world['akmal']

def test_compact_callbacks_fit_telegram_limit(world):
    tid='11111111-2222-3333-4444-555555555555'
    encoded=f'choose:{compact_id(tid)}:{compact_id(world["akmal"])}'
    assert len(encoded.encode())<=64
    assert expand_id(encoded.split(':')[1])==tid

@pytest.mark.asyncio
async def test_preapproved_receiver_connects_only_on_own_start(world):
    from aho.configure_receiver import provision
    from aho.models import Receiver
    from test_routing import targets
    from test_domain import make
    with Session.begin() as db:
        r=provision(db,98765,'Sherzod','Karomatov','expected_username',True)
        receiver_id=r.id
        assert r.connection_status=='NOT_CONNECTED'
        assert db.get(User,receiver_id).telegram_chat_id is None
    assert receiver_id not in targets(make(world))
    await dp.feed_update(bot,update('/start',uid=98766))
    with Session() as db: assert db.get(Receiver,receiver_id).connection_status=='NOT_CONNECTED'
    await dp.feed_update(bot,update('/start',uid=98765))
    with Session() as db:
        r=db.get(Receiver,receiver_id)
        assert r.connection_status=='CONNECTED'
        assert r.user.telegram_chat_id==98765
        assert r.user.telegram_username=='changed_username'
    assert receiver_id in targets(make(world))

@pytest.mark.asyncio
async def test_start_does_not_activate_disabled_or_unapproved_receiver(world):
    from aho.models import Receiver
    for receiver_status in ('PENDING_APPROVAL','DISABLED'):
        with Session.begin() as db:
            r=db.get(Receiver,world['akmal'])
            r.receiver_status=receiver_status
            r.connection_status='NOT_CONNECTED'
            r.user.telegram_chat_id=None
        await dp.feed_update(bot,update('/start',uid=1002))
        with Session() as db:
            r=db.get(Receiver,world['akmal'])
            assert r.connection_status=='NOT_CONNECTED'
            assert r.receiver_status==receiver_status

@pytest.mark.asyncio
@pytest.mark.parametrize('configured,permission,ready,deletes',[
    (True,False,False,0),(True,True,True,1),(False,False,True,0)])
async def test_polling_preserves_existing_webhook(configured,permission,ready,deletes):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from aho.bot import prepare_polling
    fake_bot=SimpleNamespace(get_webhook_info=AsyncMock(return_value=SimpleNamespace(url='https://example.org/hook' if configured else '')),delete_webhook=AsyncMock())
    assert await prepare_polling(fake_bot,permission) is ready
    assert fake_bot.delete_webhook.await_count==deletes
    if deletes:fake_bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)

@pytest.mark.asyncio
async def test_telegram_transport_uses_proxy_environment_and_verified_tls():
    import ssl
    from aho.bot_client import EnvironmentSession
    transport=EnvironmentSession()
    try:
        client=await transport.create_session()
        assert client.trust_env is True
        context=client.connector._ssl
        assert context.verify_mode==ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert await transport.create_session() is client
    finally:
        await transport.close()
