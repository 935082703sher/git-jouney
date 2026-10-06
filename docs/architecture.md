# Архитектура

```mermaid
flowchart LR
    Employee[Сотрудник] --> Telegram[Telegram Bot API]
    Specialist[Специалист АХО] --> Telegram
    Telegram <--> Bot[aiogram 3: FSM / callbacks]
    Admin[Администратор / руководитель] --> Web[React + TypeScript]
    Web --> API[FastAPI: cookies + CSRF + RBAC]
    API --> Domain[Общие доменные сервисы]
    Bot --> Domain
    Domain --> DB[(PostgreSQL)]
    DB --> Worker[Транзакционная очередь доставки]
    Worker --> Telegram
    Scheduler[APScheduler: SLA / auto-close] --> Domain
```

API, bot и worker используют одни модели и функции `services.py`. HTTP и Telegram только преобразуют ввод; полномочия, переходы, маршрутизация, история и уведомления проверяются в доменном слое. Изменения заявки и добавление outbox-записей происходят в одной транзакции.

## ER-диаграмма

```mermaid
erDiagram
    departments ||--o{ users : employs
    users ||--o{ user_roles : has
    roles ||--o{ user_roles : grants
    users ||--o| aho_specialists : receives
    aho_specialists ||--o{ receiver_categories : routes
    categories ||--o{ receiver_categories : assigned
    users ||--o{ tickets : requests
    users o|--o{ tickets : assigned_to
    categories ||--o{ tickets : categorizes
    tickets ||--o{ ticket_status_history : records
    tickets ||--o{ ticket_messages : conversation
    users ||--o{ ticket_messages : writes
    tickets ||--o{ ticket_attachments : includes
    tickets ||--o{ ticket_assignments : assignment_history
    tickets ||--o| ticket_ratings : satisfaction
    tickets o|--o{ notifications : events
    notifications ||--o{ notification_deliveries : per_recipient
    users ||--o{ notification_deliveries : recipient
    tickets ||--o{ sla_events : thresholds
    users o|--o{ audit_logs : actor
    sla_rules {
        string priority PK
        int response_minutes
        int resolution_minutes
    }
    tickets {
        uuid id PK
        string ticket_number UK
        uuid requester_id FK
        uuid category_id FK
        uuid assigned_to FK
        string status
        string priority
        string requested_urgency
        timestamp response_deadline
        timestamp resolution_deadline
        string idempotency_key UK
    }
    users {
        uuid id PK
        bigint telegram_user_id UK
        bigint telegram_chat_id UK
        string telegram_username
        string status
    }
    aho_specialists {
        uuid id PK,FK
        string receiver_status
        string connection_status
        string availability
        bool receive_requests
        bool is_fallback
        string invite_hash UK
    }
    notification_deliveries {
        uuid id PK
        uuid notification_id FK
        uuid receiver_id FK
        bigint telegram_chat_id
        bigint telegram_message_id
        string status
        int attempt_count
        timestamp next_attempt_at
    }
    system_settings {
        string key PK
        json value
    }
    ticket_counters {
        int year PK
        int value
    }
    bot_states {
        string key PK
        string state
        json data
        timestamp updated_at
    }
    bot_updates {
        bigint update_id PK
        timestamp created_at
    }
    login_attempts {
        string key PK
        int failures
        timestamp window_start
    }
```

UUID представлены строками длиной 36. Числовые Telegram ID хранятся как `BIGINT`; имена пользователей — только отображаемые данные. В `receiver_categories` составной ключ запрещает повторную связь. Уникальность `(notification_id, receiver_id)` предотвращает дубли внутри события; `(ticket_id, kind)` — повтор SLA-порога. Категории и департаменты архивируются, а не удаляются.

## Жизненный цикл

```mermaid
stateDiagram-v2
    [*] --> NEW: подтверждено создание
    NEW --> ACCEPTED: специалист принимает / руководитель назначает
    NEW --> CANCELLED: заявитель отменяет с причиной
    ACCEPTED --> IN_PROGRESS: начать
    IN_PROGRESS --> WAITING_REQUESTER: вопрос заявителю
    IN_PROGRESS --> WAITING_MATERIAL: причина ожидания
    IN_PROGRESS --> ON_HOLD: причина паузы
    WAITING_REQUESTER --> IN_PROGRESS: продолжить
    WAITING_MATERIAL --> IN_PROGRESS: продолжить
    ON_HOLD --> IN_PROGRESS: продолжить
    IN_PROGRESS --> COMPLETED: комментарий о результате
    COMPLETED --> CLOSED: подтверждение заявителя / автозакрытие
    COMPLETED --> REOPENED: проблема осталась + причина
    REOPENED --> IN_PROGRESS: повторная работа
    ACCEPTED --> CANCELLED: АХО подтверждает отмену
    IN_PROGRESS --> CANCELLED: АХО подтверждает отмену
    WAITING_REQUESTER --> CANCELLED: АХО подтверждает отмену
    WAITING_MATERIAL --> CANCELLED: АХО подтверждает отмену
    ON_HOLD --> CANCELLED: АХО подтверждает отмену
    REOPENED --> CANCELLED: АХО подтверждает отмену
    CLOSED --> [*]
    CANCELLED --> [*]
```

Заявитель по умолчанию непосредственно отменяет только `NEW`; далее он создает запрос отмены. `ACCEPTED` можно разрешить в настройках. Специалист не подтверждает результат вместо заявителя. Повторное открытие разрешено из `COMPLETED`; `CLOSED` — конечное состояние.

## Транзакции и доставка

- Годовой счетчик номера обновляется атомарным PostgreSQL `INSERT … ON CONFLICT DO UPDATE … RETURNING`.
- `Idempotency-Key` при HTTP-создании заявки связывается с внутренним ID пользователя. Telegram-мастер сохраняет один ключ подтверждения в FSM. Повтор возвращает существующую заявку.
- Операционные действия блокируют строку заявки через `FOR UPDATE OF tickets`; правила проверяются после блокировки.
- Worker выбирает одну запись outbox с `FOR UPDATE SKIP LOCKED`. При сбое транзакция откатывается; сетевые ошибки сохраняются с ограниченным backoff.
- Если Telegram сообщает о блокировке, получатель помечается `BLOCKED`; остальные доставки продолжаются.
- После принятия формируются отдельные события редактирования прежних сообщений. Их неудача не влияет на закрепление заявки.
- FSM хранится в PostgreSQL, устаревшие диалоги удаляются планировщиком. `bot_updates` отсекает повторы успешно обработанных update ID. Транзакции обработки Telegram и внешней отправки не составляют единую распределенную транзакцию; гарантия exactly-once не заявляется.
- Планировщик использует advisory lock, поэтому одновременные экземпляры backend не выполняют один цикл SLA параллельно.

## Безопасность

Argon2 для паролей; JWT в HttpOnly/SameSite=Strict cookie; CSRF-токен для изменяющих HTTP-запросов; проверка Origin при login; ограничение ошибочных попыток входа. Все данные валидируются Pydantic, SQL строится SQLAlchemy, React экранирует текст. Файлы отдаются как attachment с `nosniff`, разрешенные типы и размеры ограничены. Секреты и тела запросов не записываются в структурированные логи. У сервиса нет публичного endpoint, позволяющего выдать себя за Telegram-пользователя.

Предварительное одобрение конкретного числового Telegram ID поддерживается операторской командой `python -m aho.configure_receiver`. Она не назначает chat ID; привязка завершается только на личном `/start` от этого ID. При существующем webhook polling не включается без `TELEGRAM_REPLACE_WEBHOOK=true`. Для исходящего Telegram HTTPS используется окружение proxy/CA через `aho.bot_client.EnvironmentSession`.
