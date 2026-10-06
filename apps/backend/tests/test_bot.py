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
from aho.models import Delivery, Receiver, Attachment

@pytest.fixture
def simple_recipient(monkeypatch):
    from aho.config import settings
    monkeypatch.setattr(settings(), 'telegram_request_receiver_id', 1005)

@pytest.mark.asyncio
@pytest.mark.parametrize('with_start', [False, True])
async def test_message_without_registration_delivers_only_to_owner(world, simple_recipient, with_start):
    from aho.notifications import deliver_one, TelegramTransport
    with Session.begin() as db:
        receiver=db.get(Receiver,world['admin'])
        receiver.categories=[]; receiver.availability='BUSY'
    fake.calls.clear()
    if with_start:
        await dp.feed_update(bot,update('/start'))
        assert 'Регистрация не нужна' in fake.calls[-1].text
    event=update('Konditsioner ishlamayapti, 305-xona')
    await dp.feed_update(bot,event)
    await dp.feed_update(bot,event)
    await dp.feed_update(bot,event.model_copy(update={'update_id':next(counter)}))
    with Session() as db:
        u=db.scalar(select(User).where(User.telegram_user_id==98765))
        assert u.status=='ACTIVE' and u.role_names==['EMPLOYEE']
        assert u.mobile_phone is None and u.department_id is None
        tickets=list(db.scalars(select(Ticket)))
        assert len(tickets)==1 and tickets[0].description=='Konditsioner ishlamayapti, 305-xona'
        assert tickets[0].category.name=='АХО'
        notification=db.scalar(select(Notification).where(Notification.event=='TICKET_NEW'))
        assert '@changed_username' in notification.text and '98765' in notification.text
        deliveries=list(db.scalars(select(Delivery)))
        assert len(deliveries)==1 and deliveries[0].receiver_id==world['admin']
    fake.calls.clear()
    assert await deliver_one(TelegramTransport(bot))
    assert len(fake.calls)==1 and fake.calls[0].chat_id==1005
    assert 'Konditsioner ishlamayapti' in fake.calls[0].text

@pytest.mark.asyncio
async def test_missing_axo_recipient_does_not_broadcast_to_other_staff(world,monkeypatch):
    from aho.config import settings
    from aho.models import SystemSetting
    monkeypatch.setattr(settings(), 'telegram_request_receiver_id', 0)
    with Session.begin() as db:
        db.get(SystemSetting,'routing_mode').value='BROADCAST_ALL'
    fake.calls.clear()
    await dp.feed_update(bot,update('Printer ishlamayapti'))
    with Session() as db:
        assert not db.scalar(select(Ticket))
        assert not db.scalar(select(Delivery))
    assert 'АХО пока не настроен' in fake.calls[-1].text

@pytest.mark.asyncio
async def test_pending_employee_can_write_without_approval(world, simple_recipient):
    with Session.begin() as db: db.get(User,world['employee']).status='PENDING_APPROVAL'
    await dp.feed_update(bot,update('Printer ishlamayapti',uid=1000))
    with Session() as db:
        assert db.get(User,world['employee']).status=='ACTIVE'
        assert db.scalar(select(Ticket)).requester_id==world['employee']
        assert not db.scalar(select(Notification).where(Notification.event=='USER_APPROVED'))

@pytest.mark.asyncio
@pytest.mark.parametrize('who,telegram_id,status',[
    ('employee',1000,'DISABLED'),('akmal',1002,'PENDING_APPROVAL')])
async def test_simple_request_does_not_restore_revoked_or_staff_access(world,simple_recipient,who,telegram_id,status):
    with Session.begin() as db: db.get(User,world[who]).status=status
    fake.calls.clear()
    await dp.feed_update(bot,update('/start',uid=telegram_id))
    await dp.feed_update(bot,update('Printer ishlamayapti',uid=telegram_id))
    with Session() as db:
        assert db.get(User,world[who]).status==status
        assert not db.scalar(select(Ticket))
    assert any('отключен' in (getattr(c,'text','') or '') for c in fake.calls)

@pytest.mark.asyncio
async def test_old_registration_state_and_buttons_do_not_block_requests(world,simple_recipient):
    from aiogram.fsm.storage.base import StorageKey
    key=StorageKey(bot_id=bot.id,chat_id=98765,user_id=98765)
    await dp.storage.set_state(key,'Flow:registration')
    await dp.storage.set_data(key,{'step':2,'profile':{'first_name':'Old'}})
    await dp.feed_update(bot,update('Подтвердить'))
    with Session() as db: assert not db.scalar(select(Ticket))
    await dp.storage.set_state(key,'Flow:ticket')
    await dp.feed_update(bot,update('Chiroq ishlamayapti'))
    with Session() as db: assert db.scalar(select(Ticket)).description=='Chiroq ishlamayapti'
    assert await dp.storage.get_state(key) is None

@pytest.mark.asyncio
async def test_prompt_and_cancel_do_not_create_tickets(world,simple_recipient):
    for text in ['/start','➕ Создать заявку','Отмена','ℹ️ Помощь']:
        await dp.feed_update(bot,update(text))
    with Session() as db: assert not db.scalar(select(Ticket))
    assert not any(button.text=='👥 Регистрации' for row in fake.calls[-1].reply_markup.keyboard for button in row)

@pytest.mark.asyncio
@pytest.mark.parametrize('description', ['abc','x'*2001])
async def test_simple_request_validates_description(world,simple_recipient,description):
    await dp.feed_update(bot,update(description))
    with Session() as db:
        assert not db.scalar(select(Ticket))
        assert not db.scalar(select(Notification))

@pytest.mark.asyncio
async def test_unavailable_personal_recipient_does_not_silently_drop_request(world,simple_recipient):
    with Session.begin() as db: db.get(Receiver,world['admin']).connection_status='BLOCKED'
    fake.calls.clear()
    await dp.feed_update(bot,update('Printer ishlamayapti'))
    with Session() as db: assert not db.scalar(select(Ticket))
    assert 'временно недоступен' in fake.calls[-1].text

@pytest.mark.asyncio
async def test_group_messages_are_not_collected(world,simple_recipient):
    event=update('Printer ishlamayapti')
    event=event.model_copy(update={'message':event.message.model_copy(update={'chat':Chat(id=-100123,type='supergroup')})})
    await dp.feed_update(bot,event)
    with Session() as db:
        assert not db.scalar(select(Ticket))
        assert not db.scalar(select(User).where(User.telegram_user_id==98765))

@pytest.mark.asyncio
async def test_photo_caption_is_a_request_with_attachment(world,simple_recipient):
    from aiogram.types import PhotoSize
    event=update('unused')
    message=event.message.model_copy(update={'text':None,'caption':'Rozetka ishlamayapti',
        'photo':[PhotoSize(file_id='photo-123',file_unique_id='unique-123',width=640,height=480,file_size=100)]})
    event=event.model_copy(update={'message':message})
    await dp.feed_update(bot,event)
    with Session() as db:
        ticket=db.scalar(select(Ticket))
        assert ticket.description=='Rozetka ishlamayapti'
        assert db.scalar(select(Attachment)).ticket_id==ticket.id

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
