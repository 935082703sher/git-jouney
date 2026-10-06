import re
from typing import Literal
from pydantic import BaseModel, Field, field_validator, ConfigDict

RoleName = Literal['EMPLOYEE','AHO_SPECIALIST','AHO_MANAGER','ADMIN']
Priority = Literal['LOW','NORMAL','HIGH','CRITICAL']

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class Login(StrictModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=200)

class Profile(StrictModel):
    first_name: str = Field(min_length=2, max_length=80)
    last_name: str = Field(min_length=2, max_length=80)
    department_id: str
    internal_phone: str | None = None
    mobile_phone: str
    building: str = Field(default='', max_length=120)
    floor: str = Field(default='', max_length=20)
    room: str = Field(default='', max_length=40)
    @field_validator('first_name','last_name')
    @classmethod
    def name(cls, v):
        if not all(c.isalpha() or c in " -'’" for c in v): raise ValueError('Используйте буквы')
        return v
    @field_validator('internal_phone')
    @classmethod
    def extension(cls, v):
        if v and not re.fullmatch(r'\d{3,6}', v): raise ValueError('Внутренний номер: 3–6 цифр')
        return v or None
    @field_validator('mobile_phone')
    @classmethod
    def phone(cls, v):
        v = re.sub(r'[\s()\-]', '', v)
        if not re.fullmatch(r'\+[1-9]\d{7,14}', v): raise ValueError('Номер в международном формате, например +998901234567')
        return v

class TicketCreate(StrictModel):
    category_id: str
    description: str = Field(min_length=5, max_length=10000)
    building: str = Field(min_length=1, max_length=120)
    floor: str = Field(min_length=1, max_length=20)
    room: str = Field(min_length=1, max_length=40)
    requested_urgency: Literal['NORMAL','URGENT'] = 'NORMAL'

class TicketEdit(StrictModel):
    description: str = Field(min_length=5, max_length=10000)
    building: str = Field(min_length=1, max_length=120)
    floor: str = Field(min_length=1, max_length=20)
    room: str = Field(min_length=1, max_length=40)

class Action(StrictModel):
    comment: str = Field(default='', max_length=4000)
    assignee_id: str | None = None
    priority: Priority | None = None
    score: int | None = Field(default=None, ge=1, le=5)

class Comment(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    visibility: Literal['PUBLIC','INTERNAL'] = 'PUBLIC'

class FileInput(StrictModel):
    telegram_file_id: str = Field(min_length=1, max_length=500)
    file_name: str = Field(min_length=1, max_length=255)
    mime_type: Literal['image/jpeg','image/png','application/pdf','text/plain','video/mp4']
    size: int = Field(ge=0, le=20 * 1024 * 1024)

class CatalogInput(StrictModel):
    name: str = Field(min_length=2, max_length=120)
    active: bool = True

class UserCreate(Profile):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=200)
    roles: list[RoleName] = ['EMPLOYEE']

class UserUpdate(StrictModel):
    status: Literal['ACTIVE','PENDING_APPROVAL','DISABLED'] | None = None
    roles: list[RoleName] | None = None

class ReceiverInput(StrictModel):
    user_id: str
    role: Literal['AHO_SPECIALIST','AHO_MANAGER'] = 'AHO_SPECIALIST'
    category_ids: list[str] = []
    expected_username: str | None = Field(default=None, max_length=100)
    is_fallback: bool = False

class ReceiverUpdate(StrictModel):
    receiver_status: Literal['ACTIVE','PENDING_APPROVAL','DISABLED'] | None = None
    availability: Literal['AVAILABLE','BUSY','AWAY','VACATION','DISABLED'] | None = None
    receive_requests: bool | None = None
    receive_status_updates: bool | None = None
    receive_sla_alerts: bool | None = None
    receive_escalations: bool | None = None
    is_fallback: bool | None = None
    category_ids: list[str] | None = None

class SettingsInput(StrictModel):
    routing_mode: Literal['CATEGORY','BROADCAST_ALL','MANAGER_ONLY'] | None = None
    registration_mode: Literal['OPEN','ADMIN_APPROVAL','WHITELIST'] | None = None
    busy_receives: bool | None = None
    auto_close_hours: int | None = Field(default=None, ge=1, le=8760)
    description_max: int | None = Field(default=None, ge=5, le=10000)
    timezone: str | None = None
    cancel_direct_statuses: list[Literal['NEW','ACCEPTED']] | None = None
    whitelist_telegram_ids: list[int] | None = None
    fsm_timeout_minutes: int | None = Field(default=None, ge=5, le=10080)
    @field_validator('timezone')
    @classmethod
    def tz(cls, v):
        if v:
            from zoneinfo import ZoneInfo
            try: ZoneInfo(v)
            except Exception: raise ValueError('Неизвестный часовой пояс')
        return v

class SlaInput(StrictModel):
    response_minutes: int = Field(ge=1, le=525600)
    resolution_minutes: int = Field(ge=1, le=525600)
