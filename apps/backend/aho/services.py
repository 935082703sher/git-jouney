"""One transactional domain layer for HTTP, Telegram and scheduled work."""
from datetime import timedelta
from hashlib import sha256
import secrets
from sqlalchemy import select, func, or_, update
from sqlalchemy.dialects.postgresql import insert
from fastapi import HTTPException
from .db import now
from .models import *
from .schemas import *
from .security import staff, manager, require, has_role

STATUS_LABELS = {'NEW':'Новая','ACCEPTED':'Принята','IN_PROGRESS':'В работе','WAITING_REQUESTER':'Ожидается ответ заявителя','WAITING_MATERIAL':'Ожидаются материалы','ON_HOLD':'Приостановлена','COMPLETED':'Выполнена','CLOSED':'Закрыта','CANCELLED':'Отменена','REOPENED':'Переоткрыта'}
TRANSITIONS = {'NEW':{'ACCEPTED','CANCELLED'},'ACCEPTED':{'IN_PROGRESS','CANCELLED'},'IN_PROGRESS':{'WAITING_REQUESTER','WAITING_MATERIAL','ON_HOLD','COMPLETED','CANCELLED'},'WAITING_REQUESTER':{'IN_PROGRESS','CANCELLED'},'WAITING_MATERIAL':{'IN_PROGRESS','CANCELLED'},'ON_HOLD':{'IN_PROGRESS','CANCELLED'},'COMPLETED':{'CLOSED','REOPENED'},'REOPENED':{'IN_PROGRESS','CANCELLED'},'CLOSED':set(),'CANCELLED':set()}
ACTIVE_STATUSES = set(STATUS_LABELS) - {'CLOSED','CANCELLED','COMPLETED'}

def fail(code, message): raise HTTPException(code, message)
def setting(db, key, default=None):
    row = db.get(SystemSetting, key)
    return row.value if row else default

def audit(db, actor, action, entity_type, entity_id, old=None, new=None):
    db.add(Audit(actor_id=actor.id if actor else None, actor_role=','.join(actor.role_names) if actor else 'SYSTEM', action=action, entity_type=entity_type, entity_id=entity_id, old_value=old, new_value=new, source=db.info.get('source','SYSTEM'), ip=db.info.get('ip')))

def get_ticket(db, actor, identifier, lock=False):
    stmt = select(Ticket).where(or_(Ticket.id == identifier, Ticket.ticket_number == identifier))
    if lock: stmt = stmt.with_for_update(of=Ticket)
    ticket = db.scalar(stmt.execution_options(populate_existing=True))
    if not ticket: fail(404, 'Заявка не найдена')
    if not staff(actor) and ticket.requester_id != actor.id: fail(404, 'Заявка не найдена')
    return ticket

def user_json(u, contacts=True):
    data = {'id':u.id,'first_name':u.first_name,'last_name':u.last_name,'full_name':u.full_name,'department_id':u.department_id,'department':u.department.name if u.department else '', 'roles':u.role_names,'status':u.status,'building':u.building,'floor':u.floor,'room':u.room}
    if contacts:
        data.update(email=u.email, internal_phone=u.internal_phone, mobile_phone=u.mobile_phone, telegram_user_id=u.telegram_user_id, telegram_chat_id=u.telegram_chat_id, telegram_username=u.telegram_username, last_bot_interaction=u.last_bot_interaction)
    return data

def sla_state(t, at=None):
    at = at or now()
    if t.status == 'CANCELLED': return 'EXCLUDED'
    response = t.accepted_at or (t.closed_at if t.status=='CLOSED' else at)
    resolution = t.completed_at or t.closed_at or at
    if response > t.response_deadline or resolution > t.resolution_deadline: return 'BREACHED'
    if t.status in ACTIVE_STATUSES:
        if not t.accepted_at and at >= t.created_at + (t.response_deadline-t.created_at)*.8: return 'NEAR'
        if at >= t.created_at + (t.resolution_deadline-t.created_at)*.8: return 'NEAR'
    return 'WITHIN'

def ticket_json(t):
    return {**{k:getattr(t,k) for k in ('id','ticket_number','category_id','description','building','floor','room','requested_urgency','priority','status','assigned_to','created_at','updated_at','accepted_at','started_at','completed_at','closed_at','response_deadline','resolution_deadline','routing_required','escalated','cancellation_requested')}, 'category':t.category.name,'requester':user_json(t.requester),'assignee':{'id':t.assignee.id,'full_name':t.assignee.full_name} if t.assignee else None,'sla':sla_state(t)}

def notify(db, event, text, users, ticket=None, dedupe=None, edit_ids=None):
    dedupe = dedupe or f'{event}:{uid()}'
    if db.scalar(select(Notification).where(Notification.dedupe_key==dedupe)): return
    n = Notification(event=event,text=text,ticket_id=ticket.id if ticket else None,dedupe_key=dedupe)
    db.add(n); db.flush()
    for u in {u.id:u for u in users if u and u.status=='ACTIVE' and u.telegram_chat_id}.values():
        db.add(Delivery(notification_id=n.id,ticket_id=n.ticket_id,receiver_id=u.id,telegram_chat_id=u.telegram_chat_id,edit_message_id=(edit_ids or {}).get(u.id)))

def eligible_receivers(db, preference='receive_requests'):
    busy = setting(db,'busy_receives',False)
    return [r for r in db.scalars(select(Receiver)).unique() if r.user.status=='ACTIVE' and staff(r.user) and r.receiver_status=='ACTIVE' and r.connection_status=='CONNECTED' and r.user.telegram_chat_id and getattr(r,preference) and r.availability in (['AVAILABLE','BUSY'] if busy else ['AVAILABLE'])]

def ticket_text(t, title='🔔 НОВАЯ ЗАЯВКА'):
    from zoneinfo import ZoneInfo
    telegram = f'@{t.requester.telegram_username}' if t.requester.telegram_username else 'без username'
    return f'{title}\n{t.ticket_number}\nКатегория: {t.category.name}\nЗаявитель: {t.requester.full_name}\nTelegram: {telegram} · ID: {t.requester.telegram_user_id or "—"}\nДепартамент: {t.requester.department.name if t.requester.department else "—"}\nВнутренний: {t.requester.internal_phone or "—"}\nМобильный: {t.requester.mobile_phone or "—"}\nМесто: {t.building}, этаж {t.floor}, кабинет {t.room}\nОписание: {t.description[:2200]}\nПриоритет: {t.priority}'

def route_ticket(db, t, receiver_telegram_id=None):
    if receiver_telegram_id:
        receiver = db.scalar(select(Receiver).join(User, Receiver.id == User.id)
                             .where(User.telegram_user_id == receiver_telegram_id))
        if not (receiver and receiver.user.status == 'ACTIVE' and staff(receiver.user)
                and receiver.receiver_status == 'ACTIVE' and receiver.connection_status == 'CONNECTED'
                and receiver.user.telegram_chat_id and receiver.receive_requests):
            fail(503, 'Получатель временно недоступен. Попробуйте отправить заявку позже.')
        notify(db, 'TICKET_NEW', ticket_text(t), [receiver.user], t, f'new:{t.id}')
        return
    candidates = eligible_receivers(db)
    mode = setting(db,'routing_mode','CATEGORY')
    if mode=='BROADCAST_ALL': selected = candidates
    elif mode=='MANAGER_ONLY': selected = [r for r in candidates if manager(r.user)]
    else:
        ids = set(db.scalars(select(ReceiverCategory.receiver_id).where(ReceiverCategory.category_id==t.category_id, ReceiverCategory.is_active==True)))
        selected = [r for r in candidates if r.id in ids]
    if not selected:
        t.routing_required = True
        selected = [r for r in candidates if r.is_fallback and manager(r.user)]
    notify(db,'TICKET_NEW',ticket_text(t),[r.user for r in selected],t,f'new:{t.id}')

def emit_change(db, t, actor, comment=''):
    text = f'{t.ticket_number}\nСтатус: {STATUS_LABELS[t.status]}\nОтветственный: {t.assignee.full_name if t.assignee else "не назначен"}'
    if comment: text += f'\n{comment}'
    notify(db,'TICKET_STATUS',text,[t.requester],t)
    targets = [r.user for r in eligible_receivers(db,'receive_status_updates') if r.id==t.assigned_to and r.id != actor.id]
    if t.status=='REOPENED': targets += [r.user for r in eligible_receivers(db,'receive_escalations') if manager(r.user)]
    notify(db,'STAFF_STATUS',text,targets,t)

def transition(db, actor, t, target, comment=''):
    if target not in TRANSITIONS[t.status]: fail(409, 'Действие уже недоступно. Статус заявки изменился.')
    old = t.status; t.status = target; t.updated_at = now()
    field = {'ACCEPTED':'accepted_at','IN_PROGRESS':'started_at','COMPLETED':'completed_at','CLOSED':'closed_at'}.get(target)
    if field and (field!='started_at' or not t.started_at): setattr(t,field,now())
    if target=='REOPENED':
        t.completed_at = None
        rule = db.get(SlaRule,t.priority)
        t.resolution_deadline = now()+timedelta(minutes=rule.resolution_minutes)
    db.add(StatusHistory(ticket_id=t.id,actor_id=actor.id if actor else None,old_status=old,new_status=target,comment=comment))
    audit(db,actor,'STATUS_CHANGED','ticket',t.id,{'status':old},{'status':target,'comment':comment})
    db.flush()
    if actor: emit_change(db,t,actor,comment)

def create_ticket(db, actor, data: TicketCreate, key=None, *, receiver_telegram_id=None):
    if actor.status!='ACTIVE': fail(403,'Дождитесь одобрения регистрации')
    if len(data.description)>setting(db,'description_max',2000): fail(422,'Описание превышает допустимую длину')
    category = db.get(Category,data.category_id)
    if not category or not category.active: fail(422,'Категория недоступна')
    idem = f'{actor.id}:{key}' if key else None
    if idem:
        # Serialize identical submission keys across sessions before checking existence.
        db.execute(select(func.pg_advisory_xact_lock(int(sha256(idem.encode()).hexdigest()[:15],16))))
        existing = db.scalar(select(Ticket).where(Ticket.idempotency_key==idem))
        if existing: return existing
    year = now().year
    stmt = insert(TicketCounter).values(year=year,value=1).on_conflict_do_update(index_elements=['year'],set_={'value':TicketCounter.value+1}).returning(TicketCounter.value)
    count = db.scalar(stmt)
    priority = 'HIGH' if data.requested_urgency=='URGENT' else 'NORMAL'
    rule = db.get(SlaRule,priority)
    created = now()
    t = Ticket(**data.model_dump(),ticket_number=f'AHO-{year}-{count:06d}',requester_id=actor.id,priority=priority,created_at=created,response_deadline=created+timedelta(minutes=rule.response_minutes),resolution_deadline=created+timedelta(minutes=rule.resolution_minutes),idempotency_key=idem)
    db.add(t); db.flush(); db.refresh(t)
    db.add(StatusHistory(ticket_id=t.id,actor_id=actor.id,new_status='NEW',comment='Заявка зарегистрирована'))
    audit(db,actor,'TICKET_CREATED','ticket',t.id,new={'number':t.ticket_number})
    route_ticket(db,t,receiver_telegram_id)
    return t

def operational(actor,t):
    if not staff(actor): fail(403,'Недостаточно прав')
    if not manager(actor) and t.assigned_to != actor.id: fail(403,'Заявка назначена другому сотруднику')

def action_ticket(db, actor, identifier, action, data: Action):
    t = get_ticket(db,actor,identifier,lock=True)
    own = t.requester_id==actor.id
    comment = data.comment
    if action=='accept':
        if not staff(actor): fail(403,'Недостаточно прав')
        r = db.get(Receiver,actor.id)
        if not manager(actor) and (not r or r.receiver_status!='ACTIVE'): fail(403,'Получатель не активирован')
        if t.status!='NEW' or t.assigned_to:
            fail(409,f'Заявка уже принята сотрудником {t.assignee.full_name if t.assignee else "АХО"}.')
        t.assigned_to=actor.id; t.assignee=actor
        db.add(Assignment(ticket_id=t.id,actor_id=actor.id,assignee_id=actor.id))
        transition(db,actor,t,'ACCEPTED')
        prior = list(db.scalars(select(Delivery).join(Notification).where(Delivery.ticket_id==t.id,Notification.event=='TICKET_NEW',Delivery.status=='SENT')))
        notify(db,'TICKET_ACCEPTED_EDIT',ticket_text(t,'✅ ЗАЯВКА ПРИНЯТА')+f'\nОтветственный: {actor.full_name}',[db.get(User,d.receiver_id) for d in prior],t,edit_ids={d.receiver_id:d.telegram_message_id for d in prior})
    elif action in ('assign','reassign'):
        if not manager(actor): fail(403,'Назначение доступно руководителю')
        if t.status not in ACTIVE_STATUSES: fail(409,'Заявка уже завершена')
        u = db.get(User,data.assignee_id) if data.assignee_id else None
        r = db.get(Receiver,data.assignee_id) if data.assignee_id else None
        if not u or u.status!='ACTIVE' or not staff(u) or not r or r.receiver_status!='ACTIVE': fail(422,'Выберите активного специалиста')
        old=t.assigned_to; t.assigned_to=u.id; t.assignee=u; t.routing_required=False
        db.add(Assignment(ticket_id=t.id,actor_id=actor.id,assignee_id=u.id))
        audit(db,actor,'TICKET_ASSIGNED','ticket',t.id,{'assignee':old},{'assignee':u.id})
        if t.status=='NEW': transition(db,actor,t,'ACCEPTED')
        else: emit_change(db,t,actor,'Назначен ответственный')
    elif action=='priority':
        if not manager(actor): fail(403,'Приоритет изменяет руководитель')
        if t.status not in ACTIVE_STATUSES: fail(409,'Заявка уже завершена')
        if not data.priority: fail(422,'Укажите приоритет')
        old=t.priority; t.priority=data.priority; rule=db.get(SlaRule,t.priority)
        t.response_deadline=t.created_at+timedelta(minutes=rule.response_minutes)
        t.resolution_deadline=t.created_at+timedelta(minutes=rule.resolution_minutes)
        audit(db,actor,'PRIORITY_CHANGED','ticket',t.id,{'priority':old},{'priority':t.priority})
    elif action=='escalate':
        if not staff(actor): fail(403,'Недостаточно прав')
        if not comment: fail(422,'Укажите причину эскалации')
        t.escalated=True
        audit(db,actor,'TICKET_ESCALATED','ticket',t.id,new={'reason':comment})
        notify(db,'ESCALATION',f'{t.ticket_number}: {comment}',[r.user for r in eligible_receivers(db,'receive_escalations') if manager(r.user)],t)
    elif action in ('start','resume','request-info','wait-material','hold','complete'):
        operational(actor,t)
        target={'start':'IN_PROGRESS','resume':'IN_PROGRESS','request-info':'WAITING_REQUESTER','wait-material':'WAITING_MATERIAL','hold':'ON_HOLD','complete':'COMPLETED'}[action]
        if action not in ('start','resume') and not comment: fail(422,'Добавьте комментарий')
        if action=='request-info': add_comment(db,actor,t.id,Comment(text=comment))
        transition(db,actor,t,target,comment)
    elif action in ('confirm','reopen'):
        if not own: fail(403,'Подтвердить результат может только заявитель')
        if action=='reopen' and not comment: fail(422,'Опишите оставшуюся проблему')
        if action=='confirm': t.closed_by=actor.id; t.close_reason='REQUESTER_CONFIRMED'
        transition(db,actor,t,'CLOSED' if action=='confirm' else 'REOPENED',comment)
    elif action=='cancel':
        if not comment: fail(422,'Укажите причину отмены')
        if own and t.status not in setting(db,'cancel_direct_statuses',['NEW']):
            if t.status not in ACTIVE_STATUSES: fail(409,'Отмена недоступна')
            t.cancellation_requested=comment
            audit(db,actor,'CANCELLATION_REQUESTED','ticket',t.id,new={'reason':comment})
            targets=[t.assignee] if t.assignee else [r.user for r in eligible_receivers(db) if manager(r.user)]
            notify(db,'CANCELLATION_REQUEST',f'{t.ticket_number}: запрос отмены. {comment}',targets,t)
        else:
            if not own: operational(actor,t)
            transition(db,actor,t,'CANCELLED',comment)
            t.cancellation_requested=None
    elif action=='approve-cancel':
        operational(actor,t)
        if not t.cancellation_requested: fail(409,'Нет запроса отмены')
        transition(db,actor,t,'CANCELLED',t.cancellation_requested)
        t.cancellation_requested=None
    elif action=='rate':
        if not own or t.status!='CLOSED': fail(403,'Оценить можно только свою закрытую заявку')
        if data.score is None: fail(422,'Выберите оценку 1–5')
        if db.get(Rating,t.id): fail(409,'Оценка уже сохранена')
        db.add(Rating(ticket_id=t.id,score=data.score,comment=comment))
        audit(db,actor,'TICKET_RATED','ticket',t.id,new={'score':data.score})
    else: fail(404,'Неизвестное действие')
    db.flush()
    return t

def add_comment(db,actor,identifier,data: Comment):
    t=get_ticket(db,actor,identifier,lock=True)
    if data.visibility=='INTERNAL' and not staff(actor): fail(403,'Внутренние заметки доступны только АХО')
    m=Message(ticket_id=t.id,author_id=actor.id,**data.model_dump()); db.add(m); db.flush()
    t.updated_at=now()
    audit(db,actor,'COMMENT_ADDED','ticket',t.id,new={'message_id':m.id,'visibility':m.visibility})
    if data.visibility=='PUBLIC':
        targets=[t.requester] if actor.id!=t.requester_id else [t.assignee]
        notify(db,'COMMENT',f'{t.ticket_number}\n{actor.full_name}: {m.text}',targets,t)
    return m

def add_attachment(db,actor,identifier,data: FileInput):
    t=get_ticket(db,actor,identifier)
    a=Attachment(ticket_id=t.id,uploaded_by=actor.id,**data.model_dump()); db.add(a); db.flush()
    audit(db,actor,'ATTACHMENT_ADDED','ticket',t.id,new={'attachment_id':a.id})
    return a

def telegram_requester(db, telegram_id, chat_id, username, first_name, last_name=None):
    """Identify private-chat requesters without a registration or approval form."""
    if telegram_id != chat_id: fail(403, 'Откройте бота в личном чате')
    lock = int(sha256(f'telegram-user:{telegram_id}'.encode()).hexdigest()[:15], 16)
    db.execute(select(func.pg_advisory_xact_lock(lock)))
    user = db.scalar(select(User).where(User.telegram_user_id == telegram_id))
    if not user:
        user = User(telegram_user_id=telegram_id, telegram_chat_id=chat_id,
                    first_name=(first_name or 'Telegram')[:80], last_name=(last_name or '')[:80],
                    status='ACTIVE', roles=[db.get(Role, 'EMPLOYEE')])
        db.add(user); db.flush()
        audit(db, user, 'TELEGRAM_REQUESTER_CREATED', 'user', user.id)
    elif (user.status == 'PENDING_APPROVAL' and set(user.role_names) == {'EMPLOYEE'}
          and not db.get(Receiver, user.id)):
        user.status = 'ACTIVE'
        audit(db, user, 'REGISTRATION_REQUIREMENT_REMOVED', 'user', user.id)
    if user.status != 'ACTIVE': fail(403, 'Доступ к боту отключен. Обратитесь к администратору.')
    user.telegram_chat_id = chat_id
    user.telegram_username = username
    user.last_bot_interaction = now()
    return user

def create_simple_ticket(db, actor, description, key, receiver_telegram_id=None):
    if not receiver_telegram_id:
        fail(503, 'Получатель АХО пока не настроен. Попробуйте отправить заявку позже.')
    db.execute(insert(Category).values(id=uid(), name='АХО', active=True)
               .on_conflict_do_nothing(index_elements=['name']))
    category = db.scalar(select(Category).where(Category.name == 'АХО'))
    data = TicketCreate(category_id=category.id, description=description,
                        building='Не указано', floor='Не указан', room='Не указан')
    return create_ticket(db, actor, data, key, receiver_telegram_id=receiver_telegram_id)

def register_employee(db, telegram_id, chat_id, username, profile: Profile):
    if telegram_id!=chat_id: fail(422,'Регистрация доступна только в личном чате')
    existing=db.scalar(select(User).where(User.telegram_user_id==telegram_id))
    if existing: return existing
    dep=db.get(Department,profile.department_id)
    if not dep or not dep.active: fail(422,'Выберите действующий департамент')
    mode=setting(db,'registration_mode','ADMIN_APPROVAL')
    if mode=='WHITELIST' and telegram_id not in setting(db,'whitelist_telegram_ids',[]): fail(403,'Обратитесь к администратору для включения в список сотрудников')
    u=User(**profile.model_dump(),telegram_user_id=telegram_id,telegram_chat_id=chat_id,telegram_username=username,status='ACTIVE' if mode in ('OPEN','WHITELIST') else 'PENDING_APPROVAL',last_bot_interaction=now())
    u.roles=[db.get(Role,'EMPLOYEE')]; db.add(u); db.flush()
    audit(db,u,'USER_REGISTERED','user',u.id,new={'status':u.status})
    notify(db,'REGISTRATION',f'Регистрация: {u.full_name}. Ожидает одобрения в панели.',[r.user for r in eligible_receivers(db) if manager(r.user)])
    return u

def update_profile(db, actor, data: Profile):
    dep=db.get(Department,data.department_id)
    if not dep or not dep.active: fail(422,'Департамент недоступен')
    for k,v in data.model_dump().items(): setattr(actor,k,v)
    audit(db,actor,'PROFILE_UPDATED','user',actor.id,new={'fields':list(data.model_dump())})
    return actor

def approve_registration(db, actor, user_id):
    require(actor, 'ADMIN')
    user = db.scalar(select(User).where(User.id == user_id)
                     .with_for_update(of=User).execution_options(populate_existing=True))
    if not user:
        fail(404, 'Сотрудник не найден')
    if user.status != 'PENDING_APPROVAL':
        fail(409, 'Регистрация уже обработана. Обновите список.')
    user.status = 'ACTIVE'
    audit(db, actor, 'USER_APPROVED', 'user', user.id,
          {'status': 'PENDING_APPROVAL'}, {'status': 'ACTIVE'})
    notify(db, 'USER_APPROVED',
           'Ваша регистрация одобрена. Нажмите /start, чтобы открыть меню и создать заявку.',
           [user])
    return user

def receiver_json(db,r):
    workload=db.scalar(select(func.count()).select_from(Ticket).where(Ticket.assigned_to==r.id,Ticket.status.in_(ACTIVE_STATUSES)))
    return {**user_json(r.user),**{k:getattr(r,k) for k in ('receiver_status','connection_status','availability','receive_requests','receive_status_updates','receive_sla_alerts','receive_escalations','is_fallback','expected_username','last_delivery_error','last_delivery_attempt','telegram_delivery_status')},'category_ids':[c.id for c in r.categories],'categories':[c.name for c in r.categories],'workload':workload}

def set_categories(db,r,ids):
    cats=list(db.scalars(select(Category).where(Category.id.in_(ids),Category.active==True)))
    if len(cats)!=len(set(ids)): fail(422,'Неизвестная или архивная категория')
    r.categories=cats

def bind_telegram_start(db, user, telegram_id, chat_id, username):
    """Bind a private /start to an account already identified by numeric user ID.

    An administrator may preapprove a receiver before the first bot interaction.
    An invite-based or disabled receiver still needs its usual approval.
    """
    if user.telegram_user_id != telegram_id or telegram_id != chat_id:
        fail(403, 'Telegram не соответствует учетной записи')
    user.telegram_chat_id = chat_id
    user.telegram_username = username
    user.last_bot_interaction = now()
    receiver = db.get(Receiver, user.id)
    if (receiver and user.status == 'ACTIVE' and staff(user)
            and receiver.receiver_status == 'ACTIVE'
            and receiver.connection_status in ('NOT_CONNECTED', 'BLOCKED')):
        previous = receiver.connection_status
        receiver.connection_status = 'CONNECTED'
        audit(db, user, 'RECEIVER_CONNECTED', 'receiver', user.id,
              {'connection_status': previous}, {'connection_status': 'CONNECTED'})
    return receiver

def invite_receiver(db,actor,data: ReceiverInput):
    require(actor,'ADMIN')
    u=db.get(User,data.user_id)
    if not u: fail(404,'Сотрудник не найден')
    r=db.get(Receiver,u.id)
    if not r: r=Receiver(id=u.id); db.add(r)
    role=db.get(Role,data.role)
    if data.role not in u.role_names: u.roles.append(role)
    r.expected_username=data.expected_username; r.is_fallback=data.is_fallback
    set_categories(db,r,data.category_ids)
    token=secrets.token_urlsafe(24)
    r.invite_hash=sha256(token.encode()).hexdigest(); r.invite_expires_at=now()+timedelta(days=7)
    if u.telegram_chat_id:
        r.connection_status='PENDING_APPROVAL'
    audit(db,actor,'RECEIVER_INVITED','receiver',r.id,new={'categories':data.category_ids})
    db.flush()
    return r,token

def connect_receiver(db, token, telegram_id, chat_id, username):
    if telegram_id!=chat_id: fail(422,'Откройте бота в личном чате')
    r=db.scalar(select(Receiver).where(Receiver.invite_hash==sha256(token.encode()).hexdigest()).with_for_update(of=Receiver))
    if not r or not r.invite_expires_at or r.invite_expires_at<now(): fail(403,'Ссылка недействительна или истекла. Запросите новую у администратора.')
    conflict=db.scalar(select(User).where(User.telegram_user_id==telegram_id))
    if conflict and conflict.id!=r.id: fail(409,'Telegram уже связан с другой учетной записью. Обратитесь к администратору.')
    if r.user.telegram_user_id and r.user.telegram_user_id!=telegram_id: fail(409,'Сотрудник уже подключил другой Telegram')
    r.user.telegram_user_id=telegram_id; r.user.telegram_chat_id=chat_id; r.user.telegram_username=username
    r.user.last_bot_interaction=now(); r.connection_status='PENDING_APPROVAL'; r.receiver_status='PENDING_APPROVAL'; r.invite_hash=None
    audit(db,r.user,'RECEIVER_CONNECTED','receiver',r.id)
    notify(db,'RECEIVER_APPROVAL',f'{r.user.full_name} подключил Telegram. Требуется активация в панели.',[x.user for x in eligible_receivers(db) if manager(x.user)])
    return r

def modify_receiver(db,actor,r,data: ReceiverUpdate):
    if not manager(actor): fail(403,'Недостаточно прав')
    values=data.model_dump(exclude_none=True)
    if not has_role(actor,'ADMIN') and set(values)-{'availability','receive_requests','receive_status_updates','receive_sla_alerts','receive_escalations','category_ids'}: fail(403,'Активация доступна администратору')
    if data.receiver_status=='ACTIVE':
        if not r.user.telegram_chat_id or r.connection_status in ('NOT_CONNECTED','BLOCKED'): fail(409,'Получатель должен открыть бота и нажать Start')
        if r.user.status!='ACTIVE': fail(409,'Сначала активируйте сотрудника')
        if not staff(r.user): fail(409,'Назначьте роль АХО')
        r.connection_status='CONNECTED'
    if 'category_ids' in values: set_categories(db,r,values.pop('category_ids'))
    for k,v in values.items(): setattr(r,k,v)
    if data.receiver_status=='DISABLED': r.connection_status='DISABLED'
    audit(db,actor,'RECEIVER_UPDATED','receiver',r.id,new=data.model_dump(exclude_none=True))
    if data.receiver_status=='ACTIVE': notify(db,'RECEIVER_ACTIVE','Вы активированы как получатель АХО. Новые заявки будут приходить автоматически.',[r.user])
    return r
