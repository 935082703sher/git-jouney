import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, BigInteger, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base, now

def uid(): return str(uuid.uuid4())

class Department(Base):
    __tablename__ = 'departments'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    active: Mapped[bool] = mapped_column(default=True)

class Category(Base):
    __tablename__ = 'categories'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    active: Mapped[bool] = mapped_column(default=True)

class Role(Base):
    __tablename__ = 'roles'
    name: Mapped[str] = mapped_column(String(30), primary_key=True)

class UserRole(Base):
    __tablename__ = 'user_roles'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)
    role_name: Mapped[str] = mapped_column(ForeignKey('roles.name'), primary_key=True)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    first_name: Mapped[str] = mapped_column(String(80))
    last_name: Mapped[str] = mapped_column(String(80))
    department_id: Mapped[str | None] = mapped_column(ForeignKey('departments.id'))
    internal_phone: Mapped[str | None] = mapped_column(String(6))
    mobile_phone: Mapped[str | None] = mapped_column(String(20))
    building: Mapped[str] = mapped_column(String(120), default='')
    floor: Mapped[str] = mapped_column(String(20), default='')
    room: Mapped[str] = mapped_column(String(40), default='')
    status: Mapped[str] = mapped_column(String(30), default='PENDING_APPROVAL', index=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    telegram_username: Mapped[str | None] = mapped_column(String(100))
    last_bot_interaction: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    roles: Mapped[list[Role]] = relationship(secondary='user_roles', lazy='selectin')
    department: Mapped[Department | None] = relationship(lazy='joined')
    @property
    def full_name(self): return f'{self.last_name} {self.first_name}'
    @property
    def role_names(self): return [r.name for r in self.roles]

class Receiver(Base):
    __tablename__ = 'aho_specialists'
    id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)
    receiver_status: Mapped[str] = mapped_column(String(30), default='PENDING_APPROVAL')
    connection_status: Mapped[str] = mapped_column(String(30), default='NOT_CONNECTED')
    availability: Mapped[str] = mapped_column(String(20), default='AVAILABLE')
    receive_requests: Mapped[bool] = mapped_column(default=True)
    receive_status_updates: Mapped[bool] = mapped_column(default=True)
    receive_sla_alerts: Mapped[bool] = mapped_column(default=True)
    receive_escalations: Mapped[bool] = mapped_column(default=True)
    is_fallback: Mapped[bool] = mapped_column(default=False)
    expected_username: Mapped[str | None] = mapped_column(String(100))
    invite_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    invite_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_delivery_error: Mapped[str | None] = mapped_column(Text)
    last_delivery_attempt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_delivery_status: Mapped[str | None] = mapped_column(String(20))
    user: Mapped[User] = relationship(lazy='joined')
    categories: Mapped[list[Category]] = relationship(secondary='receiver_categories', lazy='selectin')

class ReceiverCategory(Base):
    __tablename__ = 'receiver_categories'
    receiver_id: Mapped[str] = mapped_column(ForeignKey('aho_specialists.id'), primary_key=True)
    category_id: Mapped[str] = mapped_column(ForeignKey('categories.id'), primary_key=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class SlaRule(Base):
    __tablename__ = 'sla_rules'
    priority: Mapped[str] = mapped_column(String(20), primary_key=True)
    response_minutes: Mapped[int] = mapped_column(Integer)
    resolution_minutes: Mapped[int] = mapped_column(Integer)

class SystemSetting(Base):
    __tablename__ = 'system_settings'
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | str | int | bool | list] = mapped_column(JSON)

class Ticket(Base):
    __tablename__ = 'tickets'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    ticket_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey('categories.id'), index=True)
    description: Mapped[str] = mapped_column(Text)
    building: Mapped[str] = mapped_column(String(120))
    floor: Mapped[str] = mapped_column(String(20))
    room: Mapped[str] = mapped_column(String(40))
    requested_urgency: Mapped[str] = mapped_column(String(20), default='NORMAL')
    priority: Mapped[str] = mapped_column(String(20), default='NORMAL', index=True)
    status: Mapped[str] = mapped_column(String(30), default='NEW', index=True)
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey('users.id'), index=True)
    routing_required: Mapped[bool] = mapped_column(default=False)
    escalated: Mapped[bool] = mapped_column(default=False)
    cancellation_requested: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(40))
    close_reason: Mapped[str | None] = mapped_column(String(100))
    response_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolution_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    requester: Mapped[User] = relationship(foreign_keys=[requester_id], lazy='joined')
    assignee: Mapped[User | None] = relationship(foreign_keys=[assigned_to], lazy='joined')
    category: Mapped[Category] = relationship(lazy='joined')

class TicketCounter(Base):
    __tablename__ = 'ticket_counters'
    year: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[int] = mapped_column(default=0)

class StatusHistory(Base):
    __tablename__ = 'ticket_status_history'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    ticket_id: Mapped[str] = mapped_column(ForeignKey('tickets.id'), index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'))
    old_status: Mapped[str | None] = mapped_column(String(30))
    new_status: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Message(Base):
    __tablename__ = 'ticket_messages'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    ticket_id: Mapped[str] = mapped_column(ForeignKey('tickets.id'), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    visibility: Mapped[str] = mapped_column(String(20), default='PUBLIC')
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    author: Mapped[User] = relationship(lazy='joined')

class Attachment(Base):
    __tablename__ = 'ticket_attachments'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    ticket_id: Mapped[str] = mapped_column(ForeignKey('tickets.id'), index=True)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey('users.id'))
    telegram_file_id: Mapped[str] = mapped_column(String(500))
    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Assignment(Base):
    __tablename__ = 'ticket_assignments'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    ticket_id: Mapped[str] = mapped_column(ForeignKey('tickets.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    assignee_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Rating(Base):
    __tablename__ = 'ticket_ratings'
    ticket_id: Mapped[str] = mapped_column(ForeignKey('tickets.id'), primary_key=True)
    score: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Notification(Base):
    __tablename__ = 'notifications'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey('tickets.id'), index=True)
    event: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Delivery(Base):
    __tablename__ = 'notification_deliveries'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    notification_id: Mapped[str] = mapped_column(ForeignKey('notifications.id'), index=True)
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey('tickets.id'), index=True)
    receiver_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    edit_message_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default='PENDING', index=True)
    attempt_count: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    notification: Mapped[Notification] = relationship(lazy='joined')
    __table_args__ = (UniqueConstraint('notification_id', 'receiver_id'),)

class SlaEvent(Base):
    __tablename__ = 'sla_events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    ticket_id: Mapped[str] = mapped_column(ForeignKey('tickets.id'), index=True)
    kind: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('ticket_id','kind'),)

class Audit(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'))
    actor_role: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    old_value: Mapped[dict | None] = mapped_column(JSON)
    new_value: Mapped[dict | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(20))
    ip: Mapped[str | None] = mapped_column(String(80))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)

class BotState(Base):
    __tablename__ = 'bot_states'
    key: Mapped[str] = mapped_column(String(150), primary_key=True)
    state: Mapped[str | None] = mapped_column(String(200))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class BotUpdate(Base):
    __tablename__ = 'bot_updates'
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class LoginAttempt(Base):
    __tablename__ = 'login_attempts'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
