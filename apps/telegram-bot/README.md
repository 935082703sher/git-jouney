Telegram service entrypoint: `python -m aho.bot` from `apps/backend`.
The Compose `telegram-bot` container imports the same domain services and SQLAlchemy models as the API. Bot FSM state persists in PostgreSQL. Only private chats are accepted; receiver invite links bind numeric Telegram IDs and require admin activation. Outbox polling is independent of Telegram update polling.
