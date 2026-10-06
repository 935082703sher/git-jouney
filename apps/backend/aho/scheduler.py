from datetime import timedelta
from sqlalchemy import select, func, delete
from .db import Session, now
from .models import Ticket, SlaEvent, User, BotState, BotUpdate
from .services import setting, transition, eligible_receivers, manager, notify, ACTIVE_STATUSES, audit

def tick():
    with Session.begin() as db:
        # Only one scheduler across backend replicas.
        if not db.scalar(select(func.pg_try_advisory_xact_lock(73521749))): return
        db.info['source']='SYSTEM'
        at=now(); close_hours=setting(db,'auto_close_hours',72)
        tickets=list(db.scalars(select(Ticket).where(Ticket.status.notin_(['CLOSED','CANCELLED'])).with_for_update(of=Ticket)))
        receivers=eligible_receivers(db,'receive_sla_alerts')
        for t in tickets:
            if t.status=='COMPLETED':
                if t.completed_at and at-t.completed_at>=timedelta(hours=close_hours):
                    t.closed_by='SYSTEM'; t.close_reason='AUTO_CLOSE'
                    transition(db,None,t,'CLOSED','Автоматическое закрытие после ожидания подтверждения')
                    notify(db,'AUTO_CLOSE',f'{t.ticket_number}: автоматически закрыта после ожидания подтверждения.',[t.requester],t,f'auto:{t.id}:{t.completed_at.isoformat()}')
                continue
            for metric,deadline,done in [('response',t.response_deadline,t.accepted_at),('resolution',t.resolution_deadline,t.completed_at)]:
                if done: continue
                duration=(deadline-t.created_at).total_seconds()
                ratio=(at-t.created_at).total_seconds()/max(1,duration)
                for threshold,label in [(0.8,'WARNING'),(1.0,'BREACH'),(1.2,'ESCALATION')]:
                    kind=f'{metric}:{label}:{deadline.isoformat()}'
                    if ratio<threshold or db.scalar(select(SlaEvent).where(SlaEvent.ticket_id==t.id,SlaEvent.kind==kind)): continue
                    db.add(SlaEvent(ticket_id=t.id,kind=kind))
                    targets=[r.user for r in receivers if r.id==t.assigned_to] if label=='WARNING' else [r.user for r in receivers if manager(r.user)]
                    if label=='ESCALATION':
                        t.escalated=True
                        targets=[r.user for r in eligible_receivers(db,'receive_escalations') if manager(r.user)]
                    notify(db,'ESCALATION' if label=='ESCALATION' else f'SLA_{label}',f'{t.ticket_number}: SLA {metric} — {label}',targets,t,f'sla:{t.id}:{kind}')
                    audit(db,None,f'SLA_{label}','ticket',t.id,new={'metric':metric})
        db.execute(delete(BotState).where(BotState.updated_at<at-timedelta(minutes=setting(db,'fsm_timeout_minutes',60))))
        db.execute(delete(BotUpdate).where(BotUpdate.created_at<at-timedelta(days=7)))
