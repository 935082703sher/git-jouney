# API

Интерактивная документация: `/docs`; контракт: `packages/shared-types/openapi.json`. Все `/api`-методы кроме login требуют активной учетной записи. Изменяющие запросы требуют cookie сессии и `X-CSRF-Token`, полученный при login или `/auth/me`.

| Метод | Путь | Группа |
|---|---|---|
| GET | `/health` | health |
| GET | `/health/ready` | health |
| POST | `/api/auth/login` | auth |
| GET | `/api/auth/me` | auth |
| POST | `/api/auth/logout` | auth |
| PATCH | `/api/users/me` | users |
| GET | `/api/users` | users |
| POST | `/api/users` | users |
| PATCH | `/api/users/{identifier}` | users |
| GET | `/api/catalog/{kind}` | departments, categories |
| POST | `/api/catalog/{kind}` | departments, categories |
| PATCH | `/api/catalog/{kind}/{identifier}` | departments, categories |
| GET | `/api/receivers` | admin |
| POST | `/api/receivers` | admin |
| PATCH | `/api/receivers/{identifier}` | admin |
| DELETE | `/api/receivers/{identifier}` | admin |
| POST | `/api/receivers/{identifier}/test` | admin |
| GET | `/api/tickets` | tickets |
| POST | `/api/tickets` | tickets |
| GET | `/api/tickets/{identifier}` | tickets |
| PATCH | `/api/tickets/{identifier}` | tickets |
| POST | `/api/tickets/{identifier}/actions/{action}` | tickets, assignments |
| POST | `/api/tickets/{identifier}/comments` | comments |
| POST | `/api/tickets/{identifier}/attachments` | attachments |
| GET | `/api/attachments/{identifier}/download` | attachments |
| GET | `/api/analytics` | analytics |
| GET | `/api/reports/export` | analytics |
| GET | `/api/sla` | sla |
| PATCH | `/api/sla/{priority}` | sla |
| GET | `/api/settings` | admin |
| PATCH | `/api/settings` | admin |
| GET | `/api/audit` | audit |
| GET | `/api/notifications` | admin |

## Действия над заявкой

`POST /api/tickets/{identifier}/actions/{action}`; identifier — UUID или номер заявки.

| action | Кто | Данные |
|---|---|---|
| accept | Активный специалист / руководитель | `{}`; только неназначенная NEW |
| assign, reassign | Руководитель / ADMIN | `assignee_id` |
| start, resume | Назначенный специалист / руководитель | `{}` |
| request-info | Назначенный специалист / руководитель | `comment` |
| wait-material, hold | Назначенный специалист / руководитель | `comment` |
| complete | Назначенный специалист / руководитель | `comment` |
| confirm | Заявитель | `{}` |
| reopen | Заявитель | `comment` |
| cancel | Заявитель / уполномоченный АХО | `comment`; после начала работ сотрудник просит отмену |
| approve-cancel | Назначенный специалист / руководитель | `{}`; требуется запрос отмены |
| priority | Руководитель / ADMIN | `priority`: LOW/NORMAL/HIGH/CRITICAL |
| escalate | АХО | `comment` |
| rate | Заявитель закрытой заявки | `score`: 1–5, необязательный `comment` |

Произвольная запись `status` запрещена. Тело валидируется; неизвестные поля отклоняются. Ошибки: 401 — вход, 403 — права, 404 — объект недоступен/не найден, 409 — конфликт состояния, 422 — неверные данные.

## Фильтры

Список заявок, аналитика и экспорт поддерживают `q`, `status`, `category_id`, `department_id`, `assigned_to`, `priority`, `sla`, `date_from`, `date_to`, `history`. Список также принимает `page` и `page_size` (1–100). Даты без смещения интерпретируются в часовом поясе организации.

## Вложения

`POST /api/tickets/{identifier}/attachments` объясняет необходимость загрузки через Telegram. Только доверенный бот передает file_id в доменный сервис. `GET /api/attachments/{identifier}/download` проверяет доступ к заявке и проксирует файл, не раскрывая токен бота.

## Проверки готовности

`/health` — процесс API и наличие настройки Telegram; `/health/ready` — доступность схемы PostgreSQL. Ни один из них не заявляет успешную реальную доставку Telegram.
