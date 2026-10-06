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
from .schemas import Profile, TicketCreate, Action, Comment, FileInput
from .services import *
from .security import staff, manager, has_role, require
from .bot_storage import PostgresStorage
from .notifications import delivery_loop
from .bot_client import create_bot

router=Router()
class Flow(StatesGroup):
    registration=State()
    ticket=State()
    note=State()
    search=State()
    file=State()

REG_FIELDS=['last_name','first_name','department_id','internal_phone','mobile_phone','confirm']
TICKET_FIELDS=['category_id','description','building','floor','room','attachments','requested_urgency','confirm']

def compact_id(value): return base64.urlsafe_b64encode(uuid.UUID(value).bytes).decode().rstrip('=')
def expand_id(value): return str(uuid.UUID(bytes=base64.urlsafe_b64decode(value+'==')))

def reply(rows): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x) for x in row] for row in rows],resize_keyboard=True)
def inline(rows): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t,callback_data=c) for t,c in row] for row in rows])
def menu(u,employee_mode=False):
    if staff(u) and not employee_mode:
        rows=[['📥 Новые заявки','🔧 Мои заявки'],['⏳ Ожидающие','✅ Выполненные'],['🟢 Мой статус','🔍 Найти заявку'],['Режим сотрудника']]
        if has_role(u,'ADMIN'): rows.append(['👥 Регистрации'])
        return reply(rows)
    rows=[['➕ Создать заявку','📋 Мои заявки'],['🕘 История','👤 Мой профиль'],['ℹ️ Помощь']]
    if staff(u): rows.append(['Режим АХО'])
    if has_role(u,'ADMIN'): rows.append(['👥 Регистрации'])
    return reply(rows)

def actor(db,event,active=True):
    tg=event.from_user
    u=db.scalar(select(User).where(User.telegram_user_id==tg.id))
    if not u: fail(403,'Сначала пройдите регистрацию: /start')
    if active and u.status!='ACTIVE': fail(403,'Регистрация ожидает одобрения администратора')
    u.telegram_username=tg.username; u.last_bot_interaction=now()
    db.info['source']='TELEGRAM'
    return u

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
        u=db.scalar(select(User).where(User.telegram_user_id==message.from_user.id))
        if u:
            bind_telegram_start(db,u,message.from_user.id,message.chat.id,message.from_user.username)
            if u.status!='ACTIVE': await message.answer('Регистрация ожидает одобрения администратора.'); return
            await message.answer(f'Здравствуйте, {u.first_name}!',reply_markup=menu(u)); return
    await message.answer('Добро пожаловать в систему АХО. Для подачи заявок необходимо пройти короткую регистрацию.',reply_markup=reply([['Начать регистрацию']]))

@router.message(F.text.in_({'Отмена','Главное меню','Режим АХО','Режим сотрудника'}))
async def cancel_flow(message: TelegramMessage,state: FSMContext):
    await state.clear()
    with Session.begin() as db:
        u=actor(db,message)
        await state.update_data(employee_mode=message.text=='Режим сотрудника')
        await message.answer('Главное меню',reply_markup=menu(u,message.text=='Режим сотрудника'))

def registration_page(db, admin, page=0):
    require(admin, 'ADMIN')
    if page < 0:
        fail(422, 'Недопустимая страница')
    rows = list(db.scalars(select(User).where(User.status == 'PENDING_APPROVAL')
                          .order_by(User.created_at, User.id).offset(page * 8).limit(9)))
    if not rows:
        return 'Нет регистраций, ожидающих одобрения.', inline([[('Обновить', 'registrations:0')]])
    lines = ['Ожидают одобрения администратора:']
    buttons = []
    for user in rows[:8]:
        department = user.department.name if user.department else 'Не указано'
        lines.append(f'• {user.full_name} — {department}')
        buttons.append([(f'✅ Подтвердить: {user.full_name[:45]}', f'approve-user:{user.id}')])
    navigation = []
    if page: navigation.append(('Назад', f'registrations:{page - 1}'))
    if len(rows) > 8: navigation.append(('Далее', f'registrations:{page + 1}'))
    if navigation: buttons.append(navigation)
    buttons.append([('Обновить', 'registrations:0')])
    return '\n\n'.join(lines), inline(buttons)

@router.message(F.text == '👥 Регистрации')
async def pending_registrations(message: TelegramMessage, state: FSMContext):
    with Session.begin() as db:
        text, keyboard = registration_page(db, actor(db, message))
    await state.clear()
    await message.answer(text, reply_markup=keyboard)

@router.callback_query(F.data.startswith('registrations:'))
async def pending_registrations_page(query: CallbackQuery):
    page = int(query.data.split(':')[1])
    with Session.begin() as db:
        text, keyboard = registration_page(db, actor(db, query), page)
    await query.answer()
    await query.message.answer(text, reply_markup=keyboard)

@router.callback_query(F.data.startswith('approve-user:'))
async def confirm_registration(query: CallbackQuery):
    user_id = query.data.split(':')[1]
    with Session.begin() as db:
        admin = actor(db, query)
        user = approve_registration(db, admin, user_id)
        name = user.full_name
    await query.answer('Регистрация одобрена')
    await query.message.answer(f'{name}: регистрация одобрена. Сотрудник получит уведомление и сможет создать заявку.')

async def reg_prompt(message,state):
    data=await state.get_data(); field=REG_FIELDS[data.get('step',0)]
    back=[['Назад','Отмена']]
    prompts={'last_name':'Введите вашу фамилию','first_name':'Введите ваше имя','internal_phone':'Введите внутренний рабочий номер (3–6 цифр)','mobile_phone':'Поделитесь своим номером телефона или введите его в международном формате'}
    if field=='department_id':
        with Session() as db: rows=[[x.name] for x in db.scalars(select(Department).where(Department.active==True).order_by(Department.name))]
        await message.answer('Выберите подразделение',reply_markup=reply(rows+back))
    elif field=='confirm':
        p=data['profile']
        with Session() as db: dep=db.get(Department,p['department_id']).name
        await message.answer(f"Проверьте данные:\n{p['last_name']} {p['first_name']}\n{dep}\nВнутренний: {p.get('internal_phone') or 'нет'}\nМобильный: {p['mobile_phone']}",reply_markup=reply([['Подтвердить','Изменить данные']]+back))
    elif field=='mobile_phone':
        await message.answer(prompts[field],reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Поделиться номером телефона',request_contact=True)],[KeyboardButton(text='Назад'),KeyboardButton(text='Отмена')]],resize_keyboard=True))
    else:
        rows=[['Нет внутреннего номера']] if field=='internal_phone' else []
        await message.answer(prompts[field],reply_markup=reply(rows+back))

@router.message(F.text.in_({'Начать регистрацию','Изменить профиль'}))
async def begin_registration(message: TelegramMessage,state: FSMContext):
    edit=message.text=='Изменить профиль'
    if edit:
        with Session.begin() as db: actor(db,message)
    await state.set_state(Flow.registration)
    await state.set_data({'step':0,'profile':{},'edit':edit})
    await reg_prompt(message,state)

@router.message(Flow.registration)
async def registration(message: TelegramMessage,state: FSMContext):
    data=await state.get_data(); step=data['step']; profile=data['profile']; text=(message.text or '').strip()
    if text=='Назад': await state.update_data(step=max(0,step-1)); await reg_prompt(message,state); return
    if text=='Изменить данные': await state.update_data(step=0); await reg_prompt(message,state); return
    field=REG_FIELDS[step]
    if field=='confirm':
        if text!='Подтвердить': await reg_prompt(message,state); return
        validated=Profile(**profile)
        with Session.begin() as db:
            db.info['source']='TELEGRAM'
            if data.get('edit'): u=update_profile(db,actor(db,message),validated)
            else: u=register_employee(db,message.from_user.id,message.chat.id,message.from_user.username,validated)
            active=u.status=='ACTIVE'; keyboard=menu(u) if active else reply([['/start']])
        await state.clear()
        await message.answer('Данные сохранены.' if active else 'Регистрация отправлена администратору на одобрение.',reply_markup=keyboard); return
    if field in ('first_name','last_name'):
        if len(text)<2 or len(text)>80 or not all(c.isalpha() or c in " -'’" for c in text): fail(422,'Введите имя буквами, минимум 2 символа')
        profile[field]=text
    elif field=='department_id':
        with Session() as db: dep=db.scalar(select(Department).where(Department.name==text,Department.active==True))
        if not dep: fail(422,'Выберите подразделение кнопкой')
        profile[field]=dep.id
    elif field=='internal_phone': profile[field]=None if text=='Нет внутреннего номера' else Profile.extension(text)
    elif field=='mobile_phone':
        if message.contact:
            if message.contact.user_id!=message.from_user.id: fail(422,'Поделитесь своим номером телефона')
            text='+'+message.contact.phone_number.lstrip('+')
        profile[field]=Profile.phone(text)
    await state.update_data(profile=profile,step=step+1); await reg_prompt(message,state)

async def ticket_prompt(message,state):
    data=await state.get_data(); field=TICKET_FIELDS[data.get('step',0)]; back=[['Назад','Отмена']]
    prompts={'description':'Опишите проблему или необходимую услугу (от 5 символов).','building':'Укажите корпус / здание','floor':'Укажите этаж','room':'Укажите кабинет','attachments':'Отправьте фото или файл (до 20 МБ). Можно добавить несколько, затем нажмите «Далее».','requested_urgency':'Насколько срочно требуется решить вопрос?'}
    if field=='category_id':
        with Session() as db: rows=[[c.name] for c in db.scalars(select(Category).where(Category.active==True).order_by(Category.name))]
        await message.answer('Выберите категорию обращения',reply_markup=reply(rows+back))
    elif field=='confirm':
        p=data['ticket']
        with Session() as db:
            category=db.get(Category,p['category_id']).name
            u=actor(db,message)
            similar=db.scalar(select(Ticket).where(Ticket.requester_id==u.id,Ticket.category_id==p['category_id'],Ticket.description==p['description'],Ticket.status.in_(ACTIVE_STATUSES),Ticket.created_at>now()-timedelta(days=1)).limit(1))
        preview=f"Новая заявка\n{category}\n{p['description']}\n{p['building']}, этаж {p['floor']}, кабинет {p['room']}\nСрочность: {'Срочная' if p['requested_urgency']=='URGENT' else 'Обычная'}\nВложения: {len(data.get('attachments',[]))}"
        if similar: preview+=f'\nУ вас уже есть похожая заявка {similar.ticket_number}. Можно отправить еще одну.'
        await message.answer(preview,reply_markup=reply([['Отправить заявку','Изменить']]+back))
        if similar: await message.answer('Похожая заявка',reply_markup=inline([[('Открыть существующую',f'view:{similar.id}')]]))
    else:
        rows=[['Далее','Пропустить']] if field=='attachments' else [['Обычная','Срочная']] if field=='requested_urgency' else [['Мой кабинет','Указать другое место']] if field=='building' else []
        await message.answer(prompts[field],reply_markup=reply(rows+back))

@router.message(F.text=='➕ Создать заявку')
async def begin_ticket(message: TelegramMessage,state: FSMContext):
    with Session.begin() as db: actor(db,message)
    await state.set_state(Flow.ticket); await state.set_data({'step':0,'ticket':{},'attachments':[],'submission_key':uid()})
    await ticket_prompt(message,state)

def telegram_file(message):
    if message.photo:
        f=message.photo[-1]; return FileInput(telegram_file_id=f.file_id,file_name='photo.jpg',mime_type='image/jpeg',size=f.file_size or 0)
    f=message.document or message.video
    if not f: fail(422,'Отправьте фото или документ')
    return FileInput(telegram_file_id=f.file_id,file_name=getattr(f,'file_name',None) or 'video.mp4',mime_type=f.mime_type or 'application/octet-stream',size=f.file_size or 0)

@router.message(Flow.ticket)
async def ticket_wizard(message: TelegramMessage,state: FSMContext):
    with Session.begin() as db: actor(db,message)
    data=await state.get_data(); step=data['step']; p=data['ticket']; text=(message.text or '').strip(); field=TICKET_FIELDS[step]
    if text=='Назад': await state.update_data(step=max(0,step-1)); await ticket_prompt(message,state); return
    if text=='Изменить': await state.update_data(step=0); await ticket_prompt(message,state); return
    if field=='confirm':
        if text!='Отправить заявку': await ticket_prompt(message,state); return
        with Session.begin() as db:
            u=actor(db,message); t=create_ticket(db,u,TicketCreate(**p),data['submission_key'])
            if not db.scalar(select(Attachment).where(Attachment.ticket_id==t.id)):
                for a in data['attachments']: add_attachment(db,u,t.id,FileInput(**a))
            number=t.ticket_number; identifier=t.id
        await state.clear()
        await message.answer(f'Заявка {number} зарегистрирована.\nСтатус: Новая.\nМы уведомим вас об изменении статуса.',reply_markup=inline([[('Открыть заявку',f'view:{identifier}')]]))
        await message.answer('Главное меню',reply_markup=menu(u,True)); return
    if field=='category_id':
        with Session() as db: c=db.scalar(select(Category).where(Category.name==text,Category.active==True))
        if not c: fail(422,'Выберите категорию кнопкой')
        p[field]=c.id
    elif field=='attachments':
        if text not in ('Далее','Пропустить'):
            a=telegram_file(message); files=data['attachments']
            if len(files)>=10: fail(422,'Максимум 10 вложений')
            files.append(a.model_dump()); await state.update_data(attachments=files)
            await message.answer('Файл добавлен. Отправьте следующий или нажмите «Далее».'); return
    elif field=='requested_urgency':
        if text not in ('Обычная','Срочная'): fail(422,'Выберите срочность кнопкой')
        p[field]='URGENT' if text=='Срочная' else 'NORMAL'
    elif field=='building' and text=='Мой кабинет':
        with Session.begin() as db: u=actor(db,message)
        if u.building and u.floor and u.room:
            p.update(building=u.building,floor=u.floor,room=u.room); step=TICKET_FIELDS.index('room')
        else: await message.answer('В профиле нет кабинета. Введите название здания.'); return
    elif field=='building' and text=='Указать другое место': await message.answer('Введите название здания.'); return
    else:
        with Session() as db: maximum=setting(db,'description_max',2000) if field=='description' else {'building':120,'floor':20,'room':40}[field]
        if len(text)<(5 if field=='description' else 1) or len(text)>maximum: fail(422,f'Допустимая длина: до {maximum} символов')
        p[field]=text
    await state.update_data(ticket=p,step=step+1); await ticket_prompt(message,state)

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

@router.message(F.text=='👤 Мой профиль')
async def profile_menu(message: TelegramMessage):
    with Session.begin() as db:
        u=actor(db,message)
        await message.answer(f'{u.full_name}\n{u.department.name if u.department else "—"}\nВнутренний: {u.internal_phone or "—"}\nМобильный: {u.mobile_phone or "—"}\n{u.building}, {u.floor}, {u.room}',reply_markup=reply([['Изменить профиль'],['Главное меню']]))

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
async def help_menu(message: TelegramMessage):
    await message.answer('Используйте кнопки меню. Для начала работы — /start. Для связи по заявке откройте ее и нажмите «Написать комментарий».')

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
