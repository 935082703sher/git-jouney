"""Idempotent development seed. No fabricated Telegram identities."""
from datetime import timedelta
import random
from sqlalchemy import select, func
from .db import Session, now
from .models import *
from .config import settings
from .security import hash_password

DEPARTMENTS=['Финансы','HR','IT','Юридический департамент','Продажи','Маркетинг','Бухгалтерия','АХО','AI Department','Другое']
CATEGORIES=['Ремонт и обслуживание','Канцелярия','Хозяйственные товары','Мебель','Электрика','Сантехника','Климат / кондиционер','Уборка','Перемещение / доставка','Пропуск / доступ','Компьютер / техника','Другое']
DESCRIPTIONS=['Не работает кондиционер в переговорной','Необходима бумага А4 и папки для документов','Замена ламп в рабочей зоне','Протекает кран в кухне','Переместить рабочие столы в новый кабинет','Не работает монитор на рабочем месте','Требуется дополнительная уборка','Сломано крепление офисного кресла','Оформить пропуск для нового сотрудника','Заканчиваются хозяйственные расходные материалы']

def seed():
    cfg=settings()
    if cfg.environment!='development': raise RuntimeError('Demo seed is allowed only in development')
    if not cfg.admin_initial_password or not cfg.demo_password: raise RuntimeError('Set ADMIN_INITIAL_PASSWORD and DEMO_PASSWORD')
    with Session.begin() as db:
        db.execute(select(func.pg_advisory_xact_lock(73521750)))
        for name in ['EMPLOYEE','AHO_SPECIALIST','AHO_MANAGER','ADMIN']:
            if not db.get(Role,name): db.add(Role(name=name))
        for name in DEPARTMENTS:
            if not db.scalar(select(Department).where(Department.name==name)): db.add(Department(name=name))
        for name in CATEGORIES:
            if not db.scalar(select(Category).where(Category.name==name)): db.add(Category(name=name))
        for p,resp,res in [('LOW',240,4320),('NORMAL',120,1440),('HIGH',30,480),('CRITICAL',10,120)]:
            if not db.get(SlaRule,p): db.add(SlaRule(priority=p,response_minutes=resp,resolution_minutes=res))
        defaults={'routing_mode':'CATEGORY','registration_mode':cfg.registration_mode,'busy_receives':False,'auto_close_hours':72,'description_max':2000,'timezone':'Asia/Tashkent','cancel_direct_statuses':['NEW'],'whitelist_telegram_ids':[],'fsm_timeout_minutes':60}
        for k,v in defaults.items():
            if not db.get(SystemSetting,k): db.add(SystemSetting(key=k,value=v))
        db.flush()
        if db.scalar(select(User).where(User.email==cfg.admin_initial_email)): return
        deps=list(db.scalars(select(Department).order_by(Department.name)))
        cats=list(db.scalars(select(Category).order_by(Category.name)))
        roles={r.name:r for r in db.scalars(select(Role))}; rng=random.Random(42)
        shared_hash=hash_password(cfg.demo_password)
        def add(email,first,last,role,department):
            u=User(email=email,first_name=first,last_name=last,password_hash=hash_password(cfg.admin_initial_password) if role=='ADMIN' else shared_hash,department_id=department.id,internal_phone=str(1000+rng.randint(0,8999)),mobile_phone='+998900000000',status='ACTIVE',building='Главный офис',floor='3',room='305')
            u.roles=[roles['EMPLOYEE']]+([roles[role]] if role!='EMPLOYEE' else [])
            db.add(u); db.flush(); return u
        admin=add(cfg.admin_initial_email,'Администратор','Системы','ADMIN',deps[0])
        managers=[add('manager@example.local' if i==0 else 'manager2@example.local',first,last,'AHO_MANAGER',deps[0]) for i,(first,last) in enumerate([('Дилшод','Мирзаев'),('Ольга','Соколова')])]
        specialists=[add('specialist@example.local' if i==0 else f'specialist{i+1}@example.local',first,last,'AHO_SPECIALIST',deps[0]) for i,(first,last) in enumerate([('Akmal','Raximov'),('Javohir','Karimov'),('Sardor','Aliyev'),('Сергей','Петров'),('Ирина','Ким')])]
        for i,u in enumerate([admin]+managers+specialists):
            r=Receiver(id=u.id,receiver_status='PENDING_APPROVAL',connection_status='NOT_CONNECTED',is_fallback=u in managers or u==admin)
            db.add(r); db.flush(); r.categories=cats if u in managers else [cats[i%len(cats)],cats[(i+5)%len(cats)]]
        employees=[]
        firsts=['Алексей','Мария','Дмитрий','Елена','Sherzodbek','Анна','Тимур','Алина','Рустам','Виктория']
        lasts=['Иванов','Петрова','Смирнов','Ким','Karomatov','Соколова','Рахимов','Юсупова','Алиев','Новикова']
        for i in range(30): employees.append(add('employee@example.local' if i==0 else f'employee{i+1}@example.local',firsts[i%10],lasts[i%10],'EMPLOYEE',deps[i%len(deps)]))
        states=['NEW','ACCEPTED','IN_PROGRESS','WAITING_REQUESTER','WAITING_MATERIAL','ON_HOLD','COMPLETED','CLOSED','CANCELLED','REOPENED']; at=now()
        for i in range(120):
            status=states[i%10]; created=at-timedelta(days=rng.randint(0,29),hours=rng.randint(0,20))
            p=rng.choice(['NORMAL','NORMAL','LOW','HIGH','CRITICAL']); rule=db.get(SlaRule,p)
            assignee=None if status in ('NEW','CANCELLED') else specialists[i%5]
            accepted=created+timedelta(minutes=rng.randint(5,150)) if assignee else None
            started=accepted+timedelta(minutes=10) if accepted and status!='ACCEPTED' else None
            completed=min(at-timedelta(minutes=30),created+timedelta(hours=rng.randint(2,30))) if status in ('COMPLETED','CLOSED') else None
            closed=min(at,completed+timedelta(hours=2)) if status=='CLOSED' else None
            t=Ticket(ticket_number=f'AHO-{at.year}-{i+1:06d}',requester_id=employees[i%30].id,category_id=cats[i%len(cats)].id,description=DESCRIPTIONS[i%10],building='Главный офис',floor=str(1+i%5),room=str(100+i),priority=p,status=status,assigned_to=assignee.id if assignee else None,created_at=created,updated_at=completed or started or accepted or created,accepted_at=accepted,started_at=started,completed_at=completed,closed_at=closed,response_deadline=created+timedelta(minutes=rule.response_minutes),resolution_deadline=created+timedelta(minutes=rule.resolution_minutes),closed_by=employees[i%30].id if closed else None,close_reason='REQUESTER_CONFIRMED' if closed else None)
            db.add(t); db.flush()
            history=[('NEW',created)]
            if accepted: history.append(('ACCEPTED',accepted))
            if started: history.append(('IN_PROGRESS',started))
            if status not in ('NEW','ACCEPTED','IN_PROGRESS'): history.append((status,closed or completed or (started+timedelta(minutes=30) if started else created)))
            for j,(st,date) in enumerate(history): db.add(StatusHistory(ticket_id=t.id,actor_id=employees[i%30].id if j==0 else assignee.id if assignee else employees[i%30].id,old_status=history[j-1][0] if j else None,new_status=st,comment='Демонстрационная история',created_at=date))
            if assignee: db.add(Assignment(ticket_id=t.id,actor_id=managers[0].id,assignee_id=assignee.id,created_at=accepted))
            if status=='CLOSED': db.add(Rating(ticket_id=t.id,score=rng.choice([3,4,5,5]),comment='Спасибо за помощь'))
            db.add(Audit(actor_id=admin.id,actor_role='ADMIN',action='DEMO_TICKET_CREATED',entity_type='ticket',entity_id=t.id,source='SYSTEM',timestamp=created,new_value={'demo':True}))
        db.add(TicketCounter(year=at.year,value=120))
    print('Seed complete: 30 employees, 5 specialists, 2 managers, 1 admin, 120 tickets.')

if __name__=='__main__': seed()
