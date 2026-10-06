# Shared configuration

Backend and Telegram share `aho.config.Settings` and `aho.services.setting`.

Deployment values and secrets are supplied by environment variables, documented in `.env.example`. Mutable organization policy (routing, SLA, registration, timeout, timezone, cancellation) lives in PostgreSQL and is managed through `/api/settings` and `/api/sla`.

Neither web assets nor this directory contain credentials. The admin frontend uses the same-origin `/api` proxy and reads organization timezone from `/api/auth/me`.
