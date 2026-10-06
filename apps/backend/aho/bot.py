"""aiogram 3 private-chat UI. All authorization and state transitions live in services."""
import asyncio
import logging
from datetime import timedelta
import base64
import uuid
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message as TelegramMessage, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from pydantic import ValidationError
from fastapi import HTTPException
from .config import settings
from .db import Session, now
from .models import *
from .schemas import Action, Comment, FileInput
from .services import *
from .security import staff, manager
from .bot_storage import PostgresStorage
from .notifications import delivery_loop
from .bot_client import create_bot

router=Router()
class Flow(StatesGroup):
    note=State()
    search=State()
    file=State()

REQUEST_PROMPT = 'Напишите одним сообщением, что не работает или какая помощь нужна. Заявка поступит в АХО. Можно указать кабинет и приложить фото с описанием. Регистрация не нужна.'

def compact_id(value): return base64.urlsafe_b64encode(uuid.UUID(value).bytes).decode().rstrip('=')
def expand_id(value): return str(uuid.UUID(bytes=base64.urlsafe_b64decode(value+'==')))

def reply(rows): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x) for x in row] for row in rows],resize_keyboard=True)
def inline(rows): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t,callback_data=c) for t,c in row] for row in rows])
def menu(u,employee_mode=False):
    if staff(u) and not employee_mode:
        rows=[['📥 Новые заявки','🔧 Мои заявки'],['⏳ Ожидающие','✅ Выполненные'],['🟢 Мой статус','🔍 Найти заявку'],['Режим сотрудника']]
        return reply(rows)
    rows=[['➕ Создать заявку','📋 Мои заявки'],['🕘 История','ℹ️ Помощь']]
    if staff(u): rows.append(['Режим АХО'])
    return reply(rows)

def actor(db,event):
    tg=event.from_user
    chat=event.chat if isinstance(event,TelegramMessage) else event.message.chat
    db.info['source']='TELEGRAM'
    return telegram_requester(db,tg.id,chat.id,tg.username,tg.first_name,tg.last_name)

class SafetyMiddleware(BaseMiddleware):
    async def __call__(self,handler,event,data):
        chat=event.chat if isinstance(event,TelegramMessage) else event.message.chat if isinstance(event,CallbackQuery) and event.message else None
        if not chat or chat.type!='private':
            if isinstance(event,CallbackQuery): await event.answer('Откройте бота в личном чате',show_alert=True)
            return
        try: return await handler(event,data)
        except (HTTPException,ValidationError,ValueError) as exc:
            if isinstance(exc,HTTPException): text=exc.detail
            elif isinstance(exc,ValidationError): text='Проверьте данные: '+str(exc.errors()[0]['msg'])[:250]
            else: text='Не удалось распознать данные. Проверьте ввод.'
            if isinstance(event,CallbackQuery): await event.answer(str(text)[:200],show_alert=True)
            else: await event.answer(text)
        except Exception as exc:
            logging.getLogger('aho').error('telegram_handler_failed type=%s',type(exc).__name__)
            if isinstance(event,CallbackQuery): await event.answer('Не удалось выполнить действие. Повторите попытку.',show_alert=True)
            else: await event.answer('Не удалось выполнить действие. Попробуйте еще раз или обратитесь к администратору.')

router.message.outer_middleware(SafetyMiddleware())
router.callback_query.outer_middleware(SafetyMiddleware())

@router.message(CommandStart())
async def start(message: TelegramMessage,state: FSMContext):
    await state.clear()
    args=(message.text or '').split(maxsplit=1)
    with Session.begin() as db:
        db.info['source']='TELEGRAM'
        if len(args)==2:
            r=connect_receiver(db,args[1],message.from_user.id,message.chat.id,message.from_user.username)
            await message.answer('Telegram подключен. Администратор должен активировать вас в разделе «АХО / Получатели заявок».'); return
        u=actor(db,message)
        bind_telegram_start(db,u,message.from_user.id,message.chat.id,message.from_user.username)
        keyboard=menu(u)
    await message.answer(REQUEST_PROMPT,reply_markup=keyboard)

@router.message(F.text.in_({'Отмена','Главное меню','Режим АХО','Режим сотрудника'}))
async def cancel_flow(message: TelegramMessage,state: FSMContext):
    await state.clear()
    with Session.begin() as db:
        u=actor(db,message)
        await state.update_data(employee_mode=message.text=='Режим сотрудника')
        await message.answer('Главное меню',reply_markup=menu(u,message.text=='Режим сотрудника'))

@router.message(F.text.in_({'➕ Создать заявку','Начать регистрацию','Изменить профиль','👤 Мой профиль','👥 Регистрации','Подтвердить','Изменить данные','Отправить заявку','Назад','Далее','Пропустить','ℹ️ Помощь'}))
async def request_prompt(message: TelegramMessage,state: FSMContext):
    with Session.begin() as db:
        u=actor(db,message)
        keyboard=menu(u,True)
    await state.clear()
    await message.answer(REQUEST_PROMPT,reply_markup=keyboard)

def telegram_file(message):
    if message.photo:
        f=message.photo[-1]; return FileInput(telegram_file_id=f.file_id,file_name='photo.jpg',mime_type='image/jpeg',size=f.file_size or 0)
    f=message.document or message.video
    if not f: fail(422,'Отправьте фото или документ')
    return FileInput(telegram_file_id=f.file_id,file_name=getattr(f,'file_name',None) or 'video.mp4',mime_type=f.mime_type or 'application/octet-stream',size=f.file_size or 0)

async def show_ticket(message,user_id,identifier):
    with Session.begin() as db:
        u=db.scalar(select(User).where(User.telegram_user_id==user_id))
        if not u or u.status!='ACTIVE': fail(403,'Нет доступа')
        t=get_ticket(db,u,identifier)
        text=f'{t.ticket_number} · {STATUS_LABELS[t.status]}\nКатегория: {t.category.name}\n{t.description}\n{t.building}, этаж {t.floor}, кабинет {t.room}\nОтветственный: {t.assignee.full_name if t.assignee else "не назначен"}'
        if staff(u): text+=f'\nЗаявитель: {t.requester.full_name}\nТелефон: {t.requester.mobile_phone or "—"} · внутр. {t.requester.internal_phone or "—"}'
        rows=[[('Написать комментарий',f'comment:public:{t.id}'),('Добавить файл',f'file:{t.id}')],[('История и переписка',f'thread:{t.id}')]]
        if staff(u):
            rows.append([('Внутренняя заметка',f'comment:internal:{t.id}')])
            if t.status=='NEW': rows.append([('✅ Принять',f'act:accept:{t.id}')])
            if manager(u): rows.append([('Назначить',f'assign:{t.id}'),('Приоритет',f'priority:{t.id}')])
            if t.assigned_to==u.id or manager(u):
                if t.status in ('ACCEPTED','REOPENED','WAITING_REQUESTER','WAITING_MATERIAL','ON_HOLD'): rows.append([('Начать / продолжить',f'act:start:{t.id}')])
                if t.status=='IN_PROGRESS':
                    rows.extend([[('Запросить информацию',f'comment:request-info:{t.id}')],[('Ожидаются материалы',f'comment:wait-material:{t.id}'),('Приостановить',f'comment:hold:{t.id}')],[('Завершить работу',f'comment:complete:{t.id}')]])
                if t.cancellation_requested: rows.append([('Одобрить отмену',f'act:approve-cancel:{t.id}')])
            rows.append([('Эскалировать',f'comment:escalate:{t.id}')])
        if t.requester_id==u.id:
            if t.status=='COMPLETED': rows.append([('Все выполнено',f'act:confirm:{t.id}'),('Проблема осталась',f'comment:reopen:{t.id}')])
            if t.status in ACTIVE_STATUSES: rows.append([('Отменить / запросить отмену',f'comment:cancel:{t.id}')])
            if t.status=='CLOSED' and not db.get(Rating,t.id): rows.append([(f'{i} ⭐',f'rate:{i}:{t.id}') for i in range(1,6)])
        for a in db.scalars(select(Attachment).where(Attachment.ticket_id==t.id)):
            rows.append([(f'📎 {a.file_name[:25]}',f'download:{a.id}')])
    await message.answer(text[:4000],reply_markup=inline(rows))

@router.callback_query(F.data.startswith('view:'))
async def view(query: CallbackQuery):
    await query.answer(); await show_ticket(query.message,query.from_user.id,query.data.split(':',1)[1])

@router.callback_query(F.data.startswith('thread:'))
async def thread(query: CallbackQuery):
    with Session.begin() as db:
        u=actor(db,query); t=get_ticket(db,u,query.data.split(':')[1])
        from zoneinfo import ZoneInfo
        tz=ZoneInfo(setting(db,'timezone','Asia/Tashkent'))
        lines=[f'{h.created_at.astimezone(tz).strftime("%d.%m.%Y %H:%M")} · {STATUS_LABELS[h.new_status]} · {h.comment}' for h in db.scalars(select(StatusHistory).where(StatusHistory.ticket_id==t.id).order_by(StatusHistory.created_at))]
        stmt=select(Message).where(Message.ticket_id==t.id).order_by(Message.created_at)
        if not staff(u): stmt=stmt.where(Message.visibility=='PUBLIC')
        lines += [f'{"🔒 " if m.visibility=="INTERNAL" else ""}{m.author.full_name}: {m.text}' for m in db.scalars(stmt)]
    await query.answer()
    text='\n\n'.join(lines) or 'История пока пуста'
    for offset in range(0,len(text),3900): await query.message.answer(text[offset:offset+3900])

@router.callback_query(F.data.startswith('act:'))
async def callback_action(query: CallbackQuery):
    _,action,identifier=query.data.split(':')
    with Session.begin() as db: action_ticket(db,actor(db,query),identifier,action,Action())
    await query.answer('Сохранено'); await show_ticket(query.message,query.from_user.id,identifier)

@router.callback_query(F.data.startswith('rate:'))
async def rate(query: CallbackQuery):
    _,score,identifier=query.data.split(':')
    with Session.begin() as db: action_ticket(db,actor(db,query),identifier,'rate',Action(score=int(score)))
    await query.answer('Спасибо за оценку!')

@router.callback_query(F.data.startswith('comment:'))
async def begin_note(query: CallbackQuery,state: FSMContext):
    _,action,identifier=query.data.split(':')
    with Session.begin() as db: get_ticket(db,actor(db,query),identifier)
    await state.set_state(Flow.note); await state.set_data({'action':action,'ticket_id':identifier})
    await query.answer(); await query.message.answer('Введите комментарий',reply_markup=reply([['Отмена']]))

@router.message(Flow.note)
async def save_note(message: TelegramMessage,state: FSMContext):
    data=await state.get_data(); text=(message.text or '').strip()
    if not text: fail(422,'Введите текст комментария')
    with Session.begin() as db:
        u=actor(db,message)
        if data['action'] in ('public','internal'): add_comment(db,u,data['ticket_id'],Comment(text=text,visibility='INTERNAL' if data['action']=='internal' else 'PUBLIC'))
        else: action_ticket(db,u,data['ticket_id'],data['action'],Action(comment=text))
    await state.clear(); await message.answer('Комментарий сохранен',reply_markup=menu(u)); await show_ticket(message,message.from_user.id,data['ticket_id'])

@router.callback_query(F.data.startswith('file:'))
async def begin_file(query: CallbackQuery,state: FSMContext):
    with Session.begin() as db: get_ticket(db,actor(db,query),query.data.split(':')[1])
    await state.set_state(Flow.file); await state.set_data({'ticket_id':query.data.split(':')[1]})
    await query.answer(); await query.message.answer('Отправьте фото или файл до 20 МБ',reply_markup=reply([['Отмена']]))

@router.message(Flow.file)
async def save_file(message: TelegramMessage,state: FSMContext):
    data=await state.get_data(); file=telegram_file(message)
    with Session.begin() as db: u=actor(db,message); add_attachment(db,u,data['ticket_id'],file)
    await state.clear(); await message.answer('Файл сохранен',reply_markup=menu(u))

@router.callback_query(F.data.startswith('download:'))
async def get_file(query: CallbackQuery):
    with Session.begin() as db:
        u=actor(db,query); a=db.get(Attachment,query.data.split(':')[1])
        if not a: fail(404,'Файл не найден')
        get_ticket(db,u,a.ticket_id)
        fid=a.telegram_file_id; mime=a.mime_type
    await query.answer()
    if mime.startswith('image/'): await query.message.answer_photo(fid)
    elif mime=='video/mp4': await query.message.answer_video(fid)
    else: await query.message.answer_document(fid)

@router.callback_query(F.data.startswith('assign:'))
async def assign_menu(query: CallbackQuery):
    identifier=query.data.split(':')[1]
    with Session.begin() as db:
        u=actor(db,query)
        if not manager(u): fail(403,'Назначение доступно руководителю')
        get_ticket(db,u,identifier)
        rows=[]
        for r in db.scalars(select(Receiver)).unique():
            if r.receiver_status=='ACTIVE' and r.user.status=='ACTIVE':
                # UUID + integer row index won't fit two UUIDs into Telegram's 64-byte callback limit.
                rows.append([(f'{r.user.full_name} · {receiver_json(db,r)["workload"]}',f'choose:{compact_id(identifier)}:{compact_id(r.id)}')])
    await query.answer(); await query.message.answer('Выберите ответственного',reply_markup=inline(rows))

@router.callback_query(F.data.startswith('choose:'))
async def assign_choice(query: CallbackQuery):
    with Session.begin() as db:
        u=actor(db,query)
        _,encoded_ticket,encoded_user=query.data.split(':')
        identifier=expand_id(encoded_ticket)
        action_ticket(db,u,identifier,'assign',Action(assignee_id=expand_id(encoded_user)))
    await query.answer('Назначено'); await show_ticket(query.message,query.from_user.id,identifier)

@router.callback_query(F.data.startswith('priority:'))
async def priority_menu(query: CallbackQuery):
    identifier=query.data.split(':')[1]
    with Session.begin() as db:
        u=actor(db,query)
        if not manager(u): fail(403,'Недостаточно прав')
        get_ticket(db,u,identifier)
    await query.answer(); await query.message.answer('Приоритет',reply_markup=inline([[(p,f'prio:{p}:{identifier}')] for p in ('LOW','NORMAL','HIGH','CRITICAL')]))

@router.callback_query(F.data.startswith('prio:'))
async def priority_set(query: CallbackQuery):
    _,p,identifier=query.data.split(':')
    with Session.begin() as db: action_ticket(db,actor(db,query),identifier,'priority',Action(priority=p))
    await query.answer('Приоритет сохранен')

@router.message(F.text.in_({'📋 Мои заявки','🕘 История','📥 Новые заявки','🔧 Мои заявки','⏳ Ожидающие','✅ Выполненные'}))
async def list_tickets(message: TelegramMessage):
    with Session.begin() as db:
        u=actor(db,message); stmt=select(Ticket)
        employee_view=message.text in ('📋 Мои заявки','🕘 История')
        if employee_view or not staff(u): stmt=stmt.where(Ticket.requester_id==u.id)
        elif message.text=='🔧 Мои заявки': stmt=stmt.where(Ticket.assigned_to==u.id)
        if message.text=='🕘 История': stmt=stmt.where(Ticket.status.in_(['CLOSED','CANCELLED']))
        elif message.text=='✅ Выполненные': stmt=stmt.where(Ticket.status.in_(['COMPLETED','CLOSED']))
        elif message.text=='📥 Новые заявки': stmt=stmt.where(Ticket.status=='NEW')
        elif message.text=='⏳ Ожидающие': stmt=stmt.where(Ticket.status.in_(['WAITING_REQUESTER','WAITING_MATERIAL','ON_HOLD']))
        else: stmt=stmt.where(Ticket.status.notin_(['CLOSED','CANCELLED']))
        rows=[[(f'{t.ticket_number} · {STATUS_LABELS[t.status]}',f'view:{t.id}')] for t in db.scalars(stmt.order_by(Ticket.created_at.desc()).limit(30))]
    await message.answer('Последние заявки' if rows else 'Заявок пока нет.',reply_markup=inline(rows) if rows else None)
    if message.text=='🕘 История': await message.answer('Период истории',reply_markup=inline([[('7 дней','history:7'),('30 дней','history:30'),('Все','history:0')]]))

@router.callback_query(F.data.startswith('history:'))
async def history_filter(query: CallbackQuery):
    days=int(query.data.split(':')[1])
    with Session.begin() as db:
        u=actor(db,query); stmt=select(Ticket).where(Ticket.requester_id==u.id,Ticket.status.in_(['CLOSED','CANCELLED']))
        if days: stmt=stmt.where(Ticket.created_at>=now()-timedelta(days=days))
        rows=[[(t.ticket_number,f'view:{t.id}')] for t in db.scalars(stmt.order_by(Ticket.created_at.desc()).limit(50))]
    await query.answer(); await query.message.answer('История' if rows else 'История заявок пока пуста.',reply_markup=inline(rows) if rows else None)

@router.message(F.text=='🟢 Мой статус')
async def availability_menu(message: TelegramMessage):
    with Session.begin() as db:
        u=actor(db,message)
        if not staff(u): fail(403,'Недостаточно прав')
    await message.answer('Выберите доступность',reply_markup=inline([[('🟢 Доступен','availability:AVAILABLE'),('🟡 Занят','availability:BUSY')],[('⚪ Недоступен','availability:AWAY')]]))

@router.callback_query(F.data.startswith('availability:'))
async def availability(query: CallbackQuery):
    value=query.data.split(':')[1]
    if value not in ('AVAILABLE','BUSY','AWAY'): fail(422,'Недопустимый статус')
    with Session.begin() as db:
        u=actor(db,query); r=db.get(Receiver,u.id)
        if not staff(u) or not r: fail(403,'Недостаточно прав')
        r.availability=value; audit(db,u,'AVAILABILITY_CHANGED','receiver',u.id,new={'availability':value})
    await query.answer('Доступность обновлена')

@router.message(F.text=='🔍 Найти заявку')
async def search_start(message: TelegramMessage,state: FSMContext):
    with Session.begin() as db: actor(db,message)
    await state.set_state(Flow.search); await message.answer('Введите номер заявки AHO-YYYY-XXXXXX',reply_markup=reply([['Отмена']]))

@router.message(Flow.search)
async def search_finish(message: TelegramMessage,state: FSMContext):
    await show_ticket(message,message.from_user.id,(message.text or '').strip()); await state.clear()

@router.message()
async def simple_request(message: TelegramMessage,state: FSMContext):
    text=(message.text or message.caption or '').strip()
    if not text or text.startswith('/'):
        await message.answer(REQUEST_PROMPT)
        return
    with Session.begin() as db:
        u=actor(db,message)
        ticket=create_simple_ticket(db,u,text,f'telegram:{message.chat.id}:{message.message_id}',
                                    settings().telegram_request_receiver_id)
        if message.photo or message.document or message.video:
            if not db.scalar(select(Attachment).where(Attachment.ticket_id==ticket.id)):
                add_attachment(db,u,ticket.id,telegram_file(message))
        number=ticket.ticket_number; identifier=ticket.id
        keyboard=menu(u,True)
    await state.clear()
    await message.answer(f'Заявка {number} принята. Мы уведомим вас об изменении статуса.',
                         reply_markup=inline([[('Открыть заявку',f'view:{identifier}')]]))
    await message.answer('Чтобы отправить еще одну заявку, просто напишите, что не работает.',reply_markup=keyboard)

class DuplicateUpdateMiddleware(BaseMiddleware):
    async def __call__(self,handler,event,data):
        from sqlalchemy import func
        with Session.begin() as db:
            db.execute(select(func.pg_advisory_xact_lock(event.update_id)))
            if db.get(BotUpdate,event.update_id): return
            result=await handler(event,data)
            db.add(BotUpdate(update_id=event.update_id))
            return result

async def prepare_polling(bot, replace_webhook=False):
    webhook = await bot.get_webhook_info()
    if webhook.url and not replace_webhook:
        logging.warning('Telegram startup paused: an existing webhook is configured. '
                        'Confirm migration before setting TELEGRAM_REPLACE_WEBHOOK=true.')
        return False
    if webhook.url:
        await bot.delete_webhook(drop_pending_updates=False)
    return True

async def main():
    cfg=settings()
    logging.basicConfig(level=cfg.log_level, format='%(message)s')
    if not cfg.telegram_bot_token:
        logging.warning('Telegram is not configured. Set TELEGRAM_BOT_TOKEN and restart telegram-bot. No messages will be sent.')
        # Keeps the optional transport container observable without pretending connectivity.
        await asyncio.Event().wait(); return
    bot=create_bot(cfg.telegram_bot_token)
    if not await prepare_polling(bot, cfg.telegram_replace_webhook):
        await bot.session.close()
        await asyncio.Event().wait()
        return
    dp=Dispatcher(storage=PostgresStorage()); dp.include_router(router)
    dp.update.outer_middleware(DuplicateUpdateMiddleware())
    # Single polling consumer, sequential updates; outbox runs independently.
    sender=asyncio.create_task(delivery_loop(bot))
    try:
        await dp.start_polling(bot,handle_as_tasks=False)
    finally:
        sender.cancel()
        await bot.session.close()

if __name__=='__main__': asyncio.run(main())
