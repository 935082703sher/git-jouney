from datetime import timedelta
import pytest
from sqlalchemy import select,func
from fastapi import HTTPException
from aho.db import Session,now
from aho.models import *
from aho.schemas import *
from aho.services import *
from aho.notifications import deliver_one,DeliveryError
from test_domain import make,act

class FakeTransport:
    def __init__(self,fail_for=None,permanent=False):self.sent=[];self.fail_for=fail_for;self.permanent=permanent
    async def send(self,d,t,u):
        if d.receiver_id==self.fail_for:raise DeliveryError('blocked' if self.permanent else 'temporary',permanent=self.permanent,blocked=self.permanent)
        self.sent.append((d.receiver_id,d.telegram_chat_id,d.notification.event));return 42

async def drain(transport):
    for _ in range(100):
        if not await deliver_one(transport):break

def targets(tid):
    with Session() as db:return set(db.scalars(select(Delivery.receiver_id).join(Notification).where(Delivery.ticket_id==tid,Notification.event=='TICKET_NEW')))

def test_username_alone_is_not_connection(world):
    with Session.begin() as db:
        r=db.get(Receiver,world['akmal']);r.user.telegram_user_id=None;r.user.telegram_chat_id=None;r.user.telegram_username='akmal';r.connection_status='NOT_CONNECTED'
    assert world['akmal'] not in targets(make(world))

def test_invite_connect_approve_no_username_matching(world):
    with Session.begin() as db:
        r=db.get(Receiver,world['akmal']);r.user.telegram_user_id=None;r.user.telegram_chat_id=None;r.connection_status='NOT_CONNECTED'
        admin=db.get(User,world['admin'])
        r,token=invite_receiver(db,admin,ReceiverInput(user_id=r.id,category_ids=[world['category']],expected_username='akmal'))
        r=connect_receiver(db,token,54321,54321,'different_username')
        assert r.user.telegram_user_id==54321 and r.connection_status=='PENDING_APPROVAL'
        modify_receiver(db,admin,r,ReceiverUpdate(receiver_status='ACTIVE'))
        assert r.receiver_status=='ACTIVE' and r.connection_status=='CONNECTED'
        with pytest.raises(HTTPException):connect_receiver(db,token,54322,54322,'akmal')
    assert world['akmal'] in targets(make(world))

@pytest.mark.asyncio
async def test_event_driven_numeric_delivery(world):
    tid=make(world);transport=FakeTransport();await drain(transport)
    assert world['akmal'] in {r[0] for r in transport.sent}
    assert world['javohir'] in {r[0] for r in transport.sent}
    assert all(isinstance(r[1],int) for r in transport.sent)
    with Session() as db:assert all(d.status=='SENT' and d.attempt_count==1 and d.delivered_at is None for d in db.scalars(select(Delivery).where(Delivery.ticket_id==tid)))

@pytest.mark.parametrize('availability',['VACATION','AWAY','DISABLED','BUSY'])
def test_unavailable_receivers(world,availability):
    with Session.begin() as db:db.get(Receiver,world['akmal']).availability=availability
    assert world['akmal'] not in targets(make(world))

def test_category_change_takes_effect_immediately(world):
    assert world['akmal'] in targets(make(world))
    with Session.begin() as db:set_categories(db,db.get(Receiver,world['akmal']),[world['other_category']])
    assert world['akmal'] not in targets(make(world))
    assert world['akmal'] in targets(make(world,category=world['other_category']))

def test_fallback(world):
    tid=make(world,category=world['other_category'])
    assert targets(tid)=={world['manager']}
    with Session() as db:assert db.get(Ticket,tid).routing_required and db.get(Ticket,tid).status=='NEW'

@pytest.mark.asyncio
async def test_blocked_receiver_does_not_lose_ticket_or_other_delivery(world):
    tid=make(world);transport=FakeTransport(world['akmal'],True);await drain(transport)
    with Session() as db:
        assert db.get(Ticket,tid)
        assert db.get(Receiver,world['akmal']).connection_status=='BLOCKED'
        bad=db.scalar(select(Delivery).where(Delivery.ticket_id==tid,Delivery.receiver_id==world['akmal']))
        assert bad.status=='FAILED' and bad.attempt_count==1
        assert world['javohir'] in {r[0] for r in transport.sent}

@pytest.mark.asyncio
async def test_retry_backoff_is_bounded(world):
    tid=make(world);transport=FakeTransport(world['akmal'])
    for attempt in range(1,5):
        await drain(transport)
        with Session.begin() as db:
            d=db.scalar(select(Delivery).where(Delivery.ticket_id==tid,Delivery.receiver_id==world['akmal']))
            assert d.attempt_count==attempt
            if attempt<4:
                assert d.status=='RETRYING' and d.next_attempt_at>now()
                d.next_attempt_at=now()-timedelta(seconds=1)
            else:assert d.status=='FAILED'

@pytest.mark.asyncio
async def test_accept_updates_other_messages(world):
    tid=make(world);transport=FakeTransport();await drain(transport)
    act(world,tid,'akmal','accept')
    with Session() as db:
        edits=list(db.scalars(select(Delivery).join(Notification).where(Notification.event=='TICKET_ACCEPTED_EDIT')))
        assert edits and all(d.edit_message_id==42 for d in edits)
        assert db.scalar(select(Delivery).join(Notification).where(Delivery.receiver_id==world['employee'],Notification.event=='TICKET_STATUS'))

@pytest.mark.parametrize('mode,expected',[('BROADCAST_ALL',{'akmal','javohir','manager','admin'}),('MANAGER_ONLY',{'manager','admin'})])
def test_modes(world,mode,expected):
    with Session.begin() as db:db.get(SystemSetting,'routing_mode').value=mode
    assert targets(make(world,category=world['other_category']))=={world[k] for k in expected}

@pytest.mark.asyncio
async def test_revoked_role_prevents_queued_disclosure(world):
    make(world)
    with Session.begin() as db:db.get(User,world['akmal']).roles=[db.get(Role,'EMPLOYEE')]
    transport=FakeTransport();await drain(transport)
    assert world['akmal'] not in {r[0] for r in transport.sent}
