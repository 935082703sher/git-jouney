import io
import csv
import json
import logging
import secrets
from hashlib import sha256
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, Response, HTTPException, Query, Header
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text, func
from sqlalchemy.exc import IntegrityError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from openpyxl import Workbook
from .config import settings
from .db import get_db, now
from .models import *
from .schemas import *
from .security import current_user, verify_password, hash_password, token_for, require, manager, staff
from .services import *
from .queries import filtered, analytics
from .scheduler import tick

logging.basicConfig(level=settings().log_level,format='%(message)s')
logger=logging.getLogger('aho')

@asynccontextmanager
async def lifespan(app):
    scheduler=AsyncIOScheduler()
    scheduler.add_job(tick,'interval',seconds=15,max_instances=1,coalesce=True)
    if settings().environment != 'testing': scheduler.start()
    yield
    if scheduler.running: scheduler.shutdown(wait=False)

app=FastAPI(title='АХО · Система управления заявками',version='1.0.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=[settings().frontend_url],allow_credentials=True,allow_methods=['GET','POST','PATCH','DELETE'],allow_headers=['Content-Type','X-CSRF-Token','Idempotency-Key'])

@app.middleware('http')
async def log_request(request: Request, call_next):
    request_id=secrets.token_hex(8)
    response=await call_next(request)
    response.headers['X-Request-ID']=request_id
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Cache-Control']='no-store'
    logger.info(json.dumps({'request_id':request_id,'user_id':getattr(request.state,'user_id',None),'action':request.method,'path':request.url.path,'status':response.status_code}))
    return response

@app.exception_handler(IntegrityError)
async def integrity_error(request,exc):
    return JSONResponse(status_code=409,content={'detail':'Такая запись уже существует или действие выполнено ранее'})

@app.exception_handler(Exception)
async def unexpected_error(request,exc):
    logger.error(json.dumps({'action':'unhandled_error','type':type(exc).__name__,'path':request.url.path}))
    return JSONResponse(status_code=500,content={'detail':'Не удалось выполнить действие. Повторите попытку или обратитесь к администратору.'})

def web_db(request: Request, db=Depends(get_db)):
    db.info.update(source='WEB',ip=request.client.host if request.client else None)
    return db

@app.get('/health',tags=['health'])
def health(): return {'status':'ok','telegram_configured':bool(settings().telegram_bot_token)}

@app.get('/health/ready',tags=['health'])
def ready(db=Depends(get_db)):
    db.execute(text('SELECT 1 FROM users LIMIT 1'))
    return {'status':'ready'}

@app.post('/api/auth/login',tags=['auth'])
def login(data: Login,request: Request,response: Response,db=Depends(web_db)):
    if request.headers.get('origin') not in (None,settings().frontend_url,settings().app_base_url): fail(403,'Недопустимый источник запроса')
    key=sha256(f'{request.client.host if request.client else ""}:{data.email.lower()}'.encode()).hexdigest()
    # The counter update is committed even for failed authentication.
    db.execute(select(func.pg_advisory_xact_lock(int(key[:15],16))))
    attempt=db.get(LoginAttempt,key)
    if not attempt: attempt=LoginAttempt(key=key,failures=0,window_start=now()); db.add(attempt)
    if now()-attempt.window_start>timedelta(minutes=15): attempt.failures=0; attempt.window_start=now()
    if attempt.failures>=10: fail(429,'Слишком много попыток. Повторите через 15 минут.')
    u=db.scalar(select(User).where(User.email==data.email.lower()))
    if not verify_password(data.password,u.password_hash if u else None) or not u or u.status!='ACTIVE':
        attempt.failures+=1; db.commit(); fail(401,'Неверный логин, пароль или учетная запись не активна')
    attempt.failures=0
    token,csrf=token_for(u)
    response.set_cookie('aho_session',token,httponly=True,secure=settings().cookie_secure,samesite='strict',max_age=28800,path='/')
    audit(db,u,'LOGIN','user',u.id)
    return {'user':user_json(u),'csrf':csrf}

@app.get('/api/auth/me',tags=['auth'])
def me(request: Request,u=Depends(current_user),db=Depends(web_db)):
    import jwt
    claims=jwt.decode(request.cookies['aho_session'],settings().jwt_secret,algorithms=['HS256'])
    return {'user':user_json(u),'csrf':claims['csrf'],'timezone':setting(db,'timezone','Asia/Tashkent')}

@app.post('/api/auth/logout',tags=['auth'])
def logout(response: Response,u=Depends(current_user)):
    response.delete_cookie('aho_session',path='/'); return {'ok':True}

@app.patch('/api/users/me',tags=['users'])
def profile(data: Profile,u=Depends(current_user),db=Depends(web_db)):
    return user_json(update_profile(db,u,data))

@app.get('/api/users',tags=['users'])
def users(u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN','AHO_MANAGER')
    audit(db,u,'USER_DIRECTORY_VIEWED','user',u.id)
    return [user_json(x) for x in db.scalars(select(User).order_by(User.last_name))]

@app.post('/api/users',tags=['users'],status_code=201)
def add_user(data: UserCreate,u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN')
    if not db.get(Department,data.department_id): fail(422,'Департамент не найден')
    v=User(**data.model_dump(exclude={'password','roles'}),password_hash=hash_password(data.password),status='ACTIVE')
    v.email=v.email.lower(); v.roles=[db.get(Role,r) for r in set(data.roles)]
    db.add(v); db.flush(); audit(db,u,'USER_CREATED','user',v.id,new={'roles':data.roles})
    return user_json(v)

@app.patch('/api/users/{identifier}',tags=['users'])
def change_user(identifier: str,data: UserUpdate,u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN')
    v=db.get(User,identifier)
    if not v: fail(404,'Сотрудник не найден')
    if v.id==u.id and ((data.roles is not None and 'ADMIN' not in data.roles) or (data.status and data.status!='ACTIVE')): fail(422,'Нельзя отключить собственный доступ администратора')
    approved = data.status == 'ACTIVE' and v.status == 'PENDING_APPROVAL'
    if approved: v = approve_registration(db,u,identifier)
    if data.roles is not None: v.roles=[db.get(Role,r) for r in set(data.roles)]
    if data.status: v.status=data.status
    audit(db,u,'USER_UPDATED','user',v.id,new=data.model_dump(exclude_none=True))
    if data.status=='ACTIVE' and not approved: notify(db,'USER_APPROVED','Ваша регистрация одобрена. Нажмите /start, чтобы открыть меню.',[v])
    return user_json(v)

CATALOGS={'departments':Department,'categories':Category}
@app.get('/api/catalog/{kind}',tags=['departments','categories'])
def catalog(kind: str,u=Depends(current_user),db=Depends(web_db)):
    model=CATALOGS.get(kind)
    if not model: fail(404,'Справочник не найден')
    stmt=select(model).order_by(model.name)
    if not manager(u): stmt=stmt.where(model.active==True)
    return [{'id':x.id,'name':x.name,'active':x.active} for x in db.scalars(stmt)]

@app.post('/api/catalog/{kind}',tags=['departments','categories'],status_code=201)
def add_catalog(kind: str,data: CatalogInput,u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN'); model=CATALOGS.get(kind)
    if not model: fail(404,'Справочник не найден')
    row=model(**data.model_dump()); db.add(row); db.flush()
    audit(db,u,'CATALOG_CREATED',kind,row.id,new=data.model_dump())
    return {'id':row.id,'name':row.name,'active':row.active}

@app.patch('/api/catalog/{kind}/{identifier}',tags=['departments','categories'])
def edit_catalog(kind: str,identifier: str,data: CatalogInput,u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN'); model=CATALOGS.get(kind)
    if not model: fail(404,'Справочник не найден')
    row=db.get(model,identifier)
    if not row: fail(404,'Запись не найдена')
    old={'name':row.name,'active':row.active}
    row.name=data.name; row.active=data.active
    audit(db,u,'CATALOG_UPDATED',kind,row.id,old,data.model_dump())
    return {'id':row.id,'name':row.name,'active':row.active}

@app.get('/api/receivers',tags=['admin'])
def receivers(u=Depends(current_user),db=Depends(web_db)):
    if not staff(u): fail(403,'Недостаточно прав')
    rows=list(db.scalars(select(Receiver)).unique())
    if not manager(u): return [{'id':r.id,'full_name':r.user.full_name,'workload':receiver_json(db,r)['workload']} for r in rows if r.receiver_status=='ACTIVE']
    return [receiver_json(db,r) for r in rows]

@app.post('/api/receivers',tags=['admin'])
def add_receiver(data: ReceiverInput,u=Depends(current_user),db=Depends(web_db)):
    r,token=invite_receiver(db,u,data)
    name=settings().telegram_bot_username
    return {'receiver':receiver_json(db,r),'start_parameter':token,'onboarding_link':f'https://t.me/{name}?start={token}' if name else None,'instruction':f'Откройте бота и отправьте /start {token}. Ссылка действует 7 дней; после подключения требуется активация.'}

@app.patch('/api/receivers/{identifier}',tags=['admin'])
def edit_receiver(identifier: str,data: ReceiverUpdate,u=Depends(current_user),db=Depends(web_db)):
    r=db.get(Receiver,identifier)
    if not r: fail(404,'Получатель не найден')
    return receiver_json(db,modify_receiver(db,u,r,data))

@app.delete('/api/receivers/{identifier}',tags=['admin'])
def remove_receiver(identifier: str,u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN')
    r=db.get(Receiver,identifier)
    if not r: fail(404,'Получатель не найден')
    r.receiver_status='DISABLED'; r.receive_requests=False; r.connection_status='DISABLED'
    r.user.roles=[x for x in r.user.roles if x.name not in ('AHO_SPECIALIST','AHO_MANAGER')]
    audit(db,u,'RECEIVER_PRIVILEGES_REMOVED','receiver',r.id)
    return {'ok':True}

@app.post('/api/receivers/{identifier}/test',tags=['admin'])
def test_receiver(identifier: str,u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN')
    r=db.get(Receiver,identifier)
    if not r or not r.user.telegram_chat_id or r.connection_status!='CONNECTED' or r.receiver_status!='ACTIVE': fail(409,'Получатель не подключен или не активирован')
    notify(db,'TEST','Тестовое уведомление АХО. Telegram успешно подключен к системе.',[r.user])
    audit(db,u,'RECEIVER_TEST_QUEUED','receiver',r.id)
    return {'status':'PENDING','telegram_configured':bool(settings().telegram_bot_token)}

def filter_params(q: str='',status: str='',category_id: str='',department_id: str='',assigned_to: str='',priority: str='',sla: str='',date_from: datetime | None=None,date_to: datetime | None=None,history: bool=False,db=Depends(get_db)):
    from zoneinfo import ZoneInfo
    values=locals().copy(); values.pop('db'); values.pop('ZoneInfo',None)
    tz=ZoneInfo(setting(db,'timezone','Asia/Tashkent'))
    for key in ('date_from','date_to'):
        v=values[key]
        if v and v.tzinfo is None: values[key]=v.replace(tzinfo=tz)
    return values

@app.get('/api/tickets',tags=['tickets'])
def tickets(filters=Depends(filter_params),page: int=Query(1,ge=1),page_size: int=Query(20,ge=1,le=100),u=Depends(current_user),db=Depends(web_db)):
    rows=filtered(db,u,**filters)
    return {'items':[ticket_json(t) for t in rows[(page-1)*page_size:page*page_size]],'total':len(rows),'page':page,'page_size':page_size}

@app.post('/api/tickets',tags=['tickets'],status_code=201)
def new_ticket(data: TicketCreate,idempotency_key: str | None=Header(default=None,max_length=80),u=Depends(current_user),db=Depends(web_db)):
    return ticket_json(create_ticket(db,u,data,idempotency_key))

@app.get('/api/tickets/{identifier}',tags=['tickets'])
def detail(identifier: str,u=Depends(current_user),db=Depends(web_db)):
    t=get_ticket(db,u,identifier)
    msg=select(Message).where(Message.ticket_id==t.id).order_by(Message.created_at)
    if not staff(u): msg=msg.where(Message.visibility=='PUBLIC')
    data=ticket_json(t)
    data['messages']=[{'id':m.id,'text':m.text,'visibility':m.visibility,'author':m.author.full_name,'created_at':m.created_at} for m in db.scalars(msg)]
    data['history']=[{'id':h.id,'old_status':h.old_status,'new_status':h.new_status,'comment':h.comment,'created_at':h.created_at} for h in db.scalars(select(StatusHistory).where(StatusHistory.ticket_id==t.id).order_by(StatusHistory.created_at))]
    data['attachments']=[{'id':a.id,'file_name':a.file_name,'mime_type':a.mime_type,'size':a.size} for a in db.scalars(select(Attachment).where(Attachment.ticket_id==t.id))]
    rating=db.get(Rating,t.id); data['rating']=rating.score if rating else None
    if staff(u):
        data['audit']=[{'action':a.action,'timestamp':a.timestamp,'source':a.source} for a in db.scalars(select(Audit).where(Audit.entity_id==t.id).order_by(Audit.timestamp))]
    return data

@app.patch('/api/tickets/{identifier}',tags=['tickets'])
def edit_ticket(identifier: str,data: TicketEdit,u=Depends(current_user),db=Depends(web_db)):
    t=get_ticket(db,u,identifier,lock=True)
    if t.requester_id!=u.id or t.status!='NEW': fail(403,'Редактировать можно свою новую заявку')
    if len(data.description)>setting(db,'description_max',2000): fail(422,'Описание слишком длинное')
    old={k:getattr(t,k) for k in data.model_dump()}
    for k,v in data.model_dump().items(): setattr(t,k,v)
    audit(db,u,'TICKET_UPDATED','ticket',t.id,old,data.model_dump())
    return ticket_json(t)

@app.post('/api/tickets/{identifier}/actions/{action}',tags=['tickets','assignments'])
def do_action(identifier: str,action: str,data: Action,u=Depends(current_user),db=Depends(web_db)):
    return ticket_json(action_ticket(db,u,identifier,action,data))

@app.post('/api/tickets/{identifier}/comments',tags=['comments'],status_code=201)
def comment(identifier: str,data: Comment,u=Depends(current_user),db=Depends(web_db)):
    m=add_comment(db,u,identifier,data); return {'id':m.id}

@app.post('/api/tickets/{identifier}/attachments',tags=['attachments'],status_code=201)
def attach(identifier: str,data: FileInput,u=Depends(current_user),db=Depends(web_db)):
    # File identifiers are obtained only by the trusted bot. Web clients cannot invent them.
    fail(422,'Отправьте вложение через Telegram-бот: Открыть заявку → Добавить файл')

@app.get('/api/attachments/{identifier}/download',tags=['attachments'])
async def download_attachment(identifier: str,u=Depends(current_user),db=Depends(web_db)):
    from .bot_client import create_bot
    a=db.get(Attachment,identifier)
    if not a: fail(404,'Файл не найден')
    get_ticket(db,u,a.ticket_id)
    if not settings().telegram_bot_token: fail(503,'Telegram-бот еще не подключен')
    async with create_bot(settings().telegram_bot_token) as bot:
        file=await bot.get_file(a.telegram_file_id)
        buffer=io.BytesIO(); await bot.download_file(file.file_path,destination=buffer)
    buffer.seek(0)
    from urllib.parse import quote
    return StreamingResponse(buffer,media_type='application/octet-stream',headers={'Content-Disposition':f"attachment; filename*=UTF-8''{quote(a.file_name,safe='')}"})

@app.get('/api/analytics',tags=['analytics'])
def stats(filters=Depends(filter_params),u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN','AHO_MANAGER')
    return analytics(db,filtered(db,u,**filters),setting(db,'timezone','Asia/Tashkent'))

@app.get('/api/reports/export',tags=['analytics'])
def export(format: Literal['csv','xlsx']='csv',filters=Depends(filter_params),u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN','AHO_MANAGER')
    from zoneinfo import ZoneInfo
    tz=ZoneInfo(setting(db,'timezone','Asia/Tashkent'))
    rows=filtered(db,u,**filters)
    matrix=[['Номер','Создана','Сотрудник','Департамент','Категория','Описание','Статус','Приоритет','Исполнитель','SLA']]
    for t in rows: matrix.append([t.ticket_number,t.created_at.astimezone(tz).strftime('%d.%m.%Y %H:%M'),t.requester.full_name,t.requester.department.name if t.requester.department else '',t.category.name,t.description,STATUS_LABELS[t.status],t.priority,t.assignee.full_name if t.assignee else '',sla_state(t)])
    # Spreadsheet formula injection protection applies to both export formats.
    matrix=[["'"+str(c) if str(c).lstrip().startswith(('=','+','-','@','\t','\r')) else str(c) for c in row] for row in matrix]
    if format=='csv':
        out=io.StringIO(); csv.writer(out).writerows(matrix); result=out.getvalue().encode('utf-8-sig'); mime='text/csv'
    else:
        out=io.BytesIO(); wb=Workbook(); ws=wb.active; ws.title='Заявки'
        for row in matrix: ws.append(row)
        wb.save(out); result=out.getvalue(); mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    audit(db,u,'REPORT_EXPORTED','report',u.id,new={'format':format,'count':len(rows)})
    return Response(result,media_type=mime,headers={'Content-Disposition':f'attachment; filename="aho-report.{format}"'})

@app.get('/api/sla',tags=['sla'])
def sla_rules(u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN','AHO_MANAGER')
    return [{'priority':r.priority,'response_minutes':r.response_minutes,'resolution_minutes':r.resolution_minutes} for r in db.scalars(select(SlaRule))]

@app.patch('/api/sla/{priority}',tags=['sla'])
def set_sla(priority: Priority,data: SlaInput,u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN'); r=db.get(SlaRule,priority)
    r.response_minutes=data.response_minutes; r.resolution_minutes=data.resolution_minutes
    audit(db,u,'SLA_UPDATED','sla',priority,new=data.model_dump())
    return {'ok':True}

@app.get('/api/settings',tags=['admin'])
def get_settings(u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN','AHO_MANAGER')
    return {x.key:x.value for x in db.scalars(select(SystemSetting))}

@app.patch('/api/settings',tags=['admin'])
def set_settings(data: SettingsInput,u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN')
    for k,v in data.model_dump(exclude_none=True).items():
        row=db.get(SystemSetting,k)
        if row: row.value=v
        else: db.add(SystemSetting(key=k,value=v))
    audit(db,u,'SETTINGS_UPDATED','settings',u.id,new=data.model_dump(exclude_none=True))
    return {'ok':True}

@app.get('/api/audit',tags=['audit'])
def audit_log(page: int=Query(1,ge=1),u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN')
    total=db.scalar(select(func.count()).select_from(Audit))
    items=[{k:getattr(a,k) for k in ('id','actor_id','actor_role','action','entity_type','entity_id','old_value','new_value','source','ip','timestamp')} for a in db.scalars(select(Audit).order_by(Audit.timestamp.desc()).offset((page-1)*50).limit(50))]
    return {'items':items,'total':total}

@app.get('/api/notifications',tags=['admin'])
def deliveries(page: int=Query(1,ge=1),u=Depends(current_user),db=Depends(web_db)):
    require(u,'ADMIN','AHO_MANAGER')
    rows=db.scalars(select(Delivery).order_by(Delivery.next_attempt_at.desc()).offset((page-1)*50).limit(50))
    return {'items':[{**{k:getattr(d,k) for k in ('id','ticket_id','receiver_id','telegram_chat_id','status','attempt_count','sent_at','failed_at','error_message','next_attempt_at')},'event':d.notification.event} for d in rows],'total':db.scalar(select(func.count()).select_from(Delivery)),'telegram_configured':bool(settings().telegram_bot_token)}
