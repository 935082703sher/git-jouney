# Структура проекта

Зависимости, локальные секреты `.env` и кэши исключены. Исходные текстовые файлы сохранены.

```text
AHO-project/
├── apps/
│   ├── admin-web/
│   │   ├── src/
│   │   │   ├── components/
│   │   │   │   └── ui/
│   │   │   │       └── dialog.tsx
│   │   │   ├── App.tsx
│   │   │   ├── api.ts
│   │   │   ├── main.tsx
│   │   │   ├── styles.css
│   │   │   └── types.ts
│   │   ├── tests/
│   │   │   └── smoke.mjs
│   │   ├── .dockerignore
│   │   ├── Dockerfile
│   │   ├── index.html
│   │   ├── nginx.conf
│   │   ├── package-lock.json
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   └── vite.config.ts
│   ├── backend/
│   │   ├── aho/
│   │   │   ├── __init__.py
│   │   │   ├── api.py
│   │   │   ├── bot.py
│   │   │   ├── bot_client.py
│   │   │   ├── bot_storage.py
│   │   │   ├── config.py
│   │   │   ├── configure_receiver.py
│   │   │   ├── db.py
│   │   │   ├── models.py
│   │   │   ├── notifications.py
│   │   │   ├── queries.py
│   │   │   ├── scheduler.py
│   │   │   ├── schemas.py
│   │   │   ├── security.py
│   │   │   ├── seed.py
│   │   │   └── services.py
│   │   ├── migrations/
│   │   │   ├── versions/
│   │   │   │   ├── 0001_initial.py
│   │   │   │   └── 0002_channel_delivery.py
│   │   │   └── env.py
│   │   ├── tests/
│   │   │   ├── conftest.py
│   │   │   ├── test_bot.py
│   │   │   ├── test_domain.py
│   │   │   └── test_routing.py
│   │   ├── Dockerfile
│   │   ├── alembic.ini
│   │   ├── pytest.ini
│   │   ├── requirements.lock
│   │   └── requirements.txt
│   └── telegram-bot/
│       └── README.md
├── docs/
│   ├── screenshots/
│   │   ├── dashboard.png
│   │   ├── mobile.png
│   │   └── ticket.png
│   ├── api.md
│   ├── architecture.md
│   ├── demo.md
│   ├── file-tree.md
│   └── validation.md
├── infrastructure/
│   └── compose.cloud.yml
├── myFolder/
│   └── three.txt
├── packages/
│   ├── shared-config/
│   │   └── README.md
│   └── shared-types/
│       ├── README.md
│       └── openapi.json
├── scripts/
│   ├── bootstrap.sh
│   ├── setup-local.sh
│   ├── start-cloud.sh
│   └── test.sh
├── .dockerignore
├── .env.example
├── .gitignore
├── ISHGA_TUSHIRISH.md
├── README.md
├── docker-compose.yml
├── five.txt
├── four.txt
├── one.txt
└── two.txt
```
