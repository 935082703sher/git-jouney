"""Persistent outbox. Telegram is an external transport, never the source of truth."""
import asyncio
from datetime import timedelta
from sqlalchemy import select
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from .db import Session, now
from .config import settings
from .models import Delivery, Receiver, Ticket, User
from .services import STATUS_LABELS, notify, eligible_receivers, manager, ticket_text

class DeliveryError(Exception):
    def __init__(self, message, permanent=False, blocked=False, retry_after=None):
        super().__init__(message)
        self.permanent=permanent; self.blocked=blocked; self.retry_after=retry_after

def ticket_keyboard(t, user):
    from .security import staff, manager
    buttons=[[InlineKeyboardButton(text='👁 Подробнее',callback_data=f'view:{t.id}')]]
    if staff(user) and t.status=='NEW':
        buttons.insert(0,[InlineKeyboardButton(text='✅ Принять',callback_data=f'act:accept:{t.id}')])
    if manager(user) and t.status in ('NEW','ACCEPTED','IN_PROGRESS','REOPENED'):
        buttons.append([InlineKeyboardButton(text='👤 Назначить',callback_data=f'assign:{t.id}'),InlineKeyboardButton(text='⚡ Приоритет',callback_data=f'priority:{t.id}')])
    if t.requester_id==user.id and t.status=='COMPLETED':
        buttons.append([InlineKeyboardButton(text='Все выполнено',callback_data=f'act:confirm:{t.id}'),InlineKeyboardButton(text='Проблема осталась',callback_data=f'comment:reopen:{t.id}')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

class TelegramTransport:
    def __init__(self, bot): self.bot=bot
    async def send(self,d,t,user):
        markup=ticket_keyboard(t,user) if t and user else None
        if d.receiver_id is None and settings().telegram_bot_username:
            markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text='Открыть бота АХО',url=f'https://t.me/{settings().telegram_bot_username}')]])
        text=d.notification.text
        # A pending new-ticket delivery may be overtaken by acceptance.
        if t and d.notification.event=='TICKET_NEW' and t.status!='NEW':
            text=ticket_text(t,f'ЗАЯВКА: {STATUS_LABELS[t.status]}')+f'\nОтветственный: {t.assignee.full_name if t.assignee else "—"}'
        try:
            if d.edit_message_id:
                await self.bot.edit_message_text(text[:4096],chat_id=d.telegram_chat_id,message_id=d.edit_message_id,reply_markup=markup)
                return d.edit_message_id
            result=await self.bot.send_message(d.telegram_chat_id,text[:4096],reply_markup=markup)
            return result.message_id
        except TelegramForbiddenError as e:
            raise DeliveryError('Telegram: бот заблокирован или доступ запрещен',permanent=True,blocked=True) from e
        except TelegramRetryAfter as e:
            raise DeliveryError('Telegram rate limit',retry_after=e.retry_after) from e
        except TelegramBadRequest as e:
            if 'message is not modified' in str(e): return d.edit_message_id
            raise DeliveryError('Telegram отклонил сообщение',permanent=True) from e

async def deliver_one(transport):
    with Session.begin() as db:
        d=db.scalar(select(Delivery).where(Delivery.status.in_(['PENDING','RETRYING']),Delivery.next_attempt_at<=now()).order_by(Delivery.next_attempt_at).with_for_update(skip_locked=True,of=Delivery).limit(1))
        if not d: return False
        t=db.get(Ticket,d.ticket_id) if d.ticket_id else None
        u=db.get(User,d.receiver_id) if d.receiver_id else None
        r=db.get(Receiver,d.receiver_id) if d.receiver_id else None
        # Re-check revocation immediately before disclosing ticket information.
        allowed = u and u.status=='ACTIVE'
        if d.receiver_id is None:
            allowed = d.telegram_chat_id < 0 and str(d.telegram_chat_id) == settings().aho_telegram_chat_id
        if t and u and u.id!=t.requester_id:
            from .security import staff
            allowed = allowed and staff(u) and r and r.receiver_status=='ACTIVE' and r.connection_status=='CONNECTED'
            pref={'TICKET_NEW':'receive_requests','STAFF_STATUS':'receive_status_updates','SLA_WARNING':'receive_sla_alerts','SLA_BREACH':'receive_sla_alerts','ESCALATION':'receive_escalations'}.get(d.notification.event)
            if pref: allowed=allowed and bool(getattr(r,pref))
        if not allowed:
            d.status='FAILED'; d.error_code='ACCESS_REVOKED'; d.error_message='Доступ получателя отозван'; d.failed_at=now()
            return True
        d.attempt_count+=1
        if r: r.last_delivery_attempt=now()
        try:
            message_id=await transport.send(d,t,u)
            d.telegram_message_id=message_id; d.status='SENT'; d.sent_at=now()
            # Bot API confirms send, not end-user receipt: delivered_at stays NULL.
            d.error_code=None; d.error_message=None
            if r: r.telegram_delivery_status='SENT'; r.last_delivery_error=None
        except Exception as exc:
            permanent=isinstance(exc,DeliveryError) and exc.permanent
            message=str(exc) if isinstance(exc,DeliveryError) else 'Временная ошибка транспорта Telegram'
            d.error_code=type(exc).__name__; d.error_message=message; d.failed_at=now()
            if r: r.telegram_delivery_status='FAILED'; r.last_delivery_error=message
            if isinstance(exc,DeliveryError) and exc.blocked and r:
                r.connection_status='BLOCKED'
                notify(db,'RECEIVER_BLOCKED',f'Не удалось доставить уведомление: {u.full_name} заблокировал бота.',[x.user for x in eligible_receivers(db) if manager(x.user) and x.id!=r.id])
            if permanent or d.attempt_count>=4: d.status='FAILED'
            else:
                delay=[30,120,600][d.attempt_count-1]
                if isinstance(exc,DeliveryError) and exc.retry_after: delay=max(delay,exc.retry_after)
                d.status='RETRYING'; d.next_attempt_at=now()+timedelta(seconds=delay)
        return True

async def delivery_loop(bot):
    transport=TelegramTransport(bot)
    while True:
        try:
            if not await deliver_one(transport): await asyncio.sleep(1)
        except asyncio.CancelledError: raise
        except Exception:
            import logging
            logging.getLogger('aho').error('outbox_iteration_failed')
            await asyncio.sleep(3)
