from collections import Counter
from datetime import datetime
from sqlalchemy import select, or_
from .models import Ticket, User, Rating, StatusHistory
from .security import staff
from .services import sla_state, ticket_json

def filtered(db,actor,q='',status='',category_id='',department_id='',assigned_to='',priority='',sla='',date_from=None,date_to=None,history=False):
    query=select(Ticket).join(User,User.id==Ticket.requester_id)
    if not staff(actor): query=query.where(Ticket.requester_id==actor.id)
    for field,value in [(Ticket.status,status),(Ticket.category_id,category_id),(Ticket.assigned_to,assigned_to),(Ticket.priority,priority),(User.department_id,department_id)]:
        if value: query=query.where(field==value)
    if q:
        pattern=f'%{q[:200]}%'
        query=query.where(or_(Ticket.ticket_number.ilike(pattern),Ticket.description.ilike(pattern),User.first_name.ilike(pattern),User.last_name.ilike(pattern),User.mobile_phone.ilike(pattern)))
    if date_from: query=query.where(Ticket.created_at>=date_from)
    if date_to: query=query.where(Ticket.created_at<=date_to)
    if history: query=query.where(Ticket.status.in_(['CLOSED','CANCELLED']))
    rows=list(db.scalars(query.order_by(Ticket.created_at.desc())))
    if sla: rows=[t for t in rows if sla_state(t)==sla]
    return rows

def analytics(db,rows,timezone='Asia/Tashkent'):
    from zoneinfo import ZoneInfo
    from .db import now
    tz=ZoneInfo(timezone)
    def groups(fn): return [{'name':k,'value':v} for k,v in Counter(fn(t) for t in rows).most_common()]
    response=[(t.accepted_at-t.created_at).total_seconds()/3600 for t in rows if t.accepted_at]
    resolution=[(t.completed_at-t.created_at).total_seconds()/3600 for t in rows if t.completed_at]
    ids=[t.id for t in rows]
    ratings=list(db.scalars(select(Rating.score).where(Rating.ticket_id.in_(ids)))) if ids else []
    reopened=set(db.scalars(select(StatusHistory.ticket_id).where(StatusHistory.ticket_id.in_(ids),StatusHistory.new_status=='REOPENED'))) if ids else set()
    scored=[t for t in rows if t.status!='CANCELLED']
    return {'total':len(rows),'new':sum(t.status=='NEW' for t in rows),'in_progress':sum(t.status=='IN_PROGRESS' for t in rows),'waiting':sum(t.status=='WAITING_REQUESTER' for t in rows),'overdue':sum(sla_state(t)=='BREACHED' and t.status not in ('CLOSED','CANCELLED','COMPLETED') for t in rows),'completed_today':sum(bool(t.completed_at and t.completed_at.astimezone(tz).date()==now().astimezone(tz).date()) for t in rows),'avg_response_hours':round(sum(response)/len(response),1) if response else 0,'avg_resolution_hours':round(sum(resolution)/len(resolution),1) if resolution else 0,'sla_compliance':round(100*sum(sla_state(t)!='BREACHED' for t in scored)/len(scored),1) if scored else 100,'satisfaction':round(sum(ratings)/len(ratings),1) if ratings else None,'reopen_rate':round(100*len(reopened)/len(rows),1) if rows else 0,'by_category':groups(lambda t:t.category.name),'by_department':groups(lambda t:t.requester.department.name if t.requester.department else '—'),'by_status':groups(lambda t:t.status),'by_priority':groups(lambda t:t.priority),'by_location':groups(lambda t:f'{t.building}, {t.room}'),'by_specialist':groups(lambda t:t.assignee.full_name if t.assignee else 'Не назначен'),'daily':sorted(groups(lambda t:t.created_at.astimezone(tz).strftime('%d.%m.%Y')),key=lambda x:datetime.strptime(x['name'],'%d.%m.%Y')),'weekly':sorted(groups(lambda t:t.created_at.astimezone(tz).strftime('%G-W%V')),key=lambda x:x['name']),'monthly':sorted(groups(lambda t:t.created_at.astimezone(tz).strftime('%Y-%m')),key=lambda x:x['name']),'recurring':groups(lambda t:t.description[:100])[:10]}
